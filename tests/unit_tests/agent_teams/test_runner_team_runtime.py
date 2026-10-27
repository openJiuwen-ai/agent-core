# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import uuid
from types import SimpleNamespace
from typing import (
    Any,
    AsyncIterator,
)
from unittest.mock import (
    AsyncMock,
    call,
    patch,
)

import pytest

from openjiuwen.agent_teams.agent.blueprint import TeamAgentBlueprint
from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.context import (
    get_session_id,
    reset_session_id,
    set_session_id,
)
from openjiuwen.agent_teams.schema.blueprint import (
    DeepAgentSpec,
    TeamAgentSpec,
)
from openjiuwen.agent_teams.schema.team import (
    TeamRole,
    TeamRuntimeContext,
    TeamSpec,
)
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent_team import create_agent_team_session
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.core.session.checkpointer.checkpointer import InMemoryCheckpointer
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent import (
    AgentCard,
)


async def _activate_pool_entry(manager, team_name: str, session_id: str, agent) -> None:
    """Test helper: insert an ActiveTeam directly into the manager pool."""
    from openjiuwen.agent_teams.runtime.pool import ActiveTeam, RuntimeState

    await manager.pool.add(
        ActiveTeam(
            team_name=team_name,
            agent=agent,
            current_session_id=session_id,
            state=RuntimeState.RUNNING,
        )
    )


@pytest.fixture
def isolated_checkpointer():
    original = CheckpointerFactory.get_checkpointer()
    checkpointer = InMemoryCheckpointer()
    CheckpointerFactory.set_default_checkpointer(checkpointer)
    try:
        yield checkpointer
    finally:
        CheckpointerFactory.set_default_checkpointer(original)


@pytest.fixture
def stateful_team_db():
    """Stateful fake TeamDatabase shared via the ``get_shared_db`` patch.

    The dispatch path now queries ``db.team.team_exists`` for the
    authoritative ``team_in_db`` signal. Tests using ``FakeTeamAgent``
    bypass production's ``BuildTeamTool`` (which is what writes the
    ``team_info`` row in round 1), so they must mark the team as
    persisted by hand — call ``await fake.team.create_team(...)`` after
    round 1 to flip ``team_exists`` for the next round.
    """
    created: set[str] = set()

    async def _create_team(team_name, **_kwargs):
        created.add(team_name)
        return True

    async def _team_exists(team_name):
        return team_name in created

    fake = AsyncMock()
    fake.initialize = AsyncMock()
    fake.drop_session_tables_by_id = AsyncMock(return_value=[])
    fake.team = AsyncMock()
    fake.team.create_team = AsyncMock(side_effect=_create_team)
    fake.team.team_exists = AsyncMock(side_effect=_team_exists)
    fake.team.delete_team = AsyncMock(return_value=True)

    with patch(
            "openjiuwen.agent_teams.spawn.shared_resources.get_shared_db",
            return_value=fake,
    ):
        yield fake


class FakeTeamAgent:
    def __init__(self, team_name: str, *, stream_label: str, spec: TeamAgentSpec | None = None):
        self.team_name = team_name
        self.stream_label = stream_label
        self.spec = spec
        self.resume_calls: list[str] = []
        self.pause_calls = 0
        self.cancel_calls = 0
        self.stop_calls = 0
        self.recover_calls = 0
        self.interactions: list[str] = []
        self.invoke_calls = 0
        self.stream_calls = 0
        self.stream_inputs: list[Any] = []

    def persist_session_manifest(self, session) -> None:
        from openjiuwen.agent_teams.runtime.metadata import write_team_namespace

        write_team_namespace(
            session,
            self.team_name,
            {
                "spec": {"team_name": self.team_name},
                "context": {
                    "role": "leader",
                    "member_name": "leader",
                    "desc": "leader",
                    "team_spec": {
                        "team_name": self.team_name,
                        "display_name": self.team_name,
                        "leader_member_name": "leader",
                    },
                    "messager_config": None,
                    "db_config": {
                        "db_type": "sqlite",
                        "connection_string": ":memory:",
                    },
                },
            },
        )

    async def invoke(self, inputs: Any, session=None) -> Any:
        self.invoke_calls += 1
        self.persist_session_manifest(session)
        return {"team_name": self.team_name, "session_id": session.get_session_id()}

    async def stream(self, inputs: Any, session=None) -> AsyncIterator[Any]:
        self.stream_calls += 1
        self.stream_inputs.append(inputs)
        await self.invoke(inputs, session=session)
        yield OutputSchema(
            type="message",
            index=1,
            payload={"event_type": self.stream_label, "session_id": session.get_session_id()},
        )

    async def resume_for_new_session(self, session) -> None:
        self.resume_calls.append(session.get_session_id())

    async def recover_for_existing_session(self, session) -> None:
        self.resume_calls.append(session.get_session_id())
        self.stop_calls += 1

    async def recover_team(self) -> list[str]:
        self.recover_calls += 1
        return []

    async def _pause_coordination(self) -> None:
        self.pause_calls += 1

    async def pause_coordination(self) -> None:
        await self._pause_coordination()

    async def interact(self, message: str) -> None:
        self.interactions.append(message)

    async def cancel_agent(self) -> None:
        self.cancel_calls += 1

    async def _stop_coordination(self) -> None:
        self.stop_calls += 1

    async def stop_coordination(self) -> None:
        await self._stop_coordination()


def _runtime_spec(team_name: str) -> TeamAgentSpec:
    """Build a TeamAgentSpec for runtime tests that are not exercising team.plan."""
    return TeamAgentSpec.model_construct(
        team_name=team_name,
        agents={},
        enable_team_plan=False,
    )


@pytest.mark.asyncio
async def test_runner_run_agent_team_streaming_accepts_spec_and_emits_runtime_ready(
        isolated_checkpointer,
        stateful_team_db,
):
    await Runner.start()
    session_id = f"team_spec_{uuid.uuid4().hex}"
    spec = _runtime_spec("spec_team")
    agent = FakeTeamAgent("spec_team", stream_label="team.chunk")

    with patch.object(TeamAgentSpec, "build", return_value=agent):
        chunks = [
            chunk
            async for chunk in Runner.run_agent_team_streaming(
                agent_team=spec,
                inputs={"query": "hello"},
                session=session_id,
            )
        ]

    assert len(chunks) == 2
    assert chunks[0].payload["event_type"] == "team.runtime_ready"
    assert chunks[0].payload["activation_kind"] == "create"
    assert chunks[0].payload["team_name"] == "spec_team"
    assert chunks[0].payload["session_id"] == session_id
    assert chunks[1].payload["event_type"] == "team.chunk"

    await Runner.stop()
    await isolated_checkpointer.release(session_id)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_runner_run_agent_team_streaming_flushes_team_manifest_before_runtime_ready(isolated_checkpointer):
    from openjiuwen.agent_teams.runtime.metadata import read_team_namespace

    await Runner.start()
    session_id = f"team_manifest_{uuid.uuid4().hex}"
    team_name = "manifest_team"
    spec = _runtime_spec(team_name)
    agent = FakeTeamAgent(team_name, stream_label="team.chunk")

    stream = Runner.run_agent_team_streaming(
        agent_team=spec,
        inputs={"query": "hello"},
        session=session_id,
    )
    try:
        with patch.object(TeamAgentSpec, "build", return_value=agent):
            ready_chunk = await stream.__anext__()

        assert ready_chunk.payload["event_type"] == "team.runtime_ready"

        restored = create_agent_team_session(session_id=session_id)
        await restored.pre_run()
        bucket = read_team_namespace(restored, team_name)

        assert bucket is not None
        assert bucket["spec"]["team_name"] == team_name
        assert bucket["context"]["db_config"]["connection_string"] == ":memory:"
    finally:
        await stream.aclose()
        await Runner.stop()
        await isolated_checkpointer.release(session_id)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_runner_run_agent_team_streaming_without_stream_logger(isolated_checkpointer, tmp_path):
    """Omitting ``stream_logger`` leaves no diagnostic file behind."""
    await Runner.start()
    session_id = f"team_spec_{uuid.uuid4().hex}"
    spec = _runtime_spec("spec_team")
    agent = FakeTeamAgent("spec_team", stream_label="team.chunk")
    target = tmp_path / "stream.log"

    with patch.object(TeamAgentSpec, "build", return_value=agent):
        chunks = [
            chunk
            async for chunk in Runner.run_agent_team_streaming(
                agent_team=spec,
                inputs={"query": "hello"},
                session=session_id,
            )
        ]

    assert len(chunks) == 2
    assert not target.exists()

    await Runner.stop()
    await isolated_checkpointer.release(session_id)


@pytest.mark.asyncio
async def test_runner_team_plan_uses_leader_stream_without_outer_approval_gate(
        isolated_checkpointer,
        stateful_team_db,
        tmp_path,
):
    await Runner.start()
    session_id = f"team_plan_{uuid.uuid4().hex}"
    spec = TeamAgentSpec.model_construct(
        team_name="plan_gate_team",
        agents={},
        enable_team_plan=True,
        workspace=SimpleNamespace(root_path=str(tmp_path)),
        predefined_members=[],
    )
    agent = FakeTeamAgent("plan_gate_team", stream_label="team.chunk")

    with patch.object(TeamAgentSpec, "build", return_value=agent):
        chunks = [
            chunk
            async for chunk in Runner.run_agent_team_streaming(
                agent_team=spec,
                inputs={"query": "do work", "request_id": "req"},
                session=session_id,
            )
        ]

    assert [chunk.payload["event_type"] for chunk in chunks] == ["team.runtime_ready", "team.chunk"]
    payloads = [chunk.payload for chunk in chunks if isinstance(chunk.payload, dict)]
    assert all(payload.get("source") != "team_plan_approval" for payload in payloads)
    assert all("_approved_team_inputs" not in payload for payload in payloads)
    assert all("_team_plan_rejected" not in payload for payload in payloads)
    assert agent.stream_inputs
    assert agent.stream_inputs[0]["query"] == "do work"

    await Runner.stop()
    await isolated_checkpointer.release(session_id)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_runner_run_agent_team_streaming_with_stream_logger_writes_file(isolated_checkpointer, tmp_path):
    """Passing a ``TeamStreamLogger`` writes aggregated records to its file
    without perturbing the streamed chunks."""
    from openjiuwen.agent_teams.monitor import TeamStreamLogger

    await Runner.start()
    session_id = f"team_spec_{uuid.uuid4().hex}"
    spec = _runtime_spec("spec_team")
    agent = FakeTeamAgent("spec_team", stream_label="team.chunk")
    log_path = tmp_path / "stream.log"
    stream_logger = TeamStreamLogger(log_path)

    with patch.object(TeamAgentSpec, "build", return_value=agent):
        chunks = [
            chunk
            async for chunk in Runner.run_agent_team_streaming(
                agent_team=spec,
                inputs={"query": "hello"},
                session=session_id,
                stream_logger=stream_logger,
            )
        ]

    # Stream is unperturbed by the observer.
    assert len(chunks) == 2
    assert chunks[0].payload["event_type"] == "team.runtime_ready"
    assert chunks[1].payload["event_type"] == "team.chunk"
    # File got the runtime_ready record at minimum; flush ran in finally.
    text = log_path.read_text(encoding="utf-8")
    assert "category=runtime_ready" in text
    assert "stream end" in text

    await Runner.stop()
    await isolated_checkpointer.release(session_id)


@pytest.mark.asyncio
async def test_team_harness_seeds_team_plan_mode_on_start(
        isolated_checkpointer,
        tmp_path,
):
    """Initial-plan-mode leader seeds plan mode on the child session at start.

    Seeding moved out of the removed ``run_streaming`` into the start path
    (``_seed_initial_plan_mode`` on the child session bound for the cycle), so
    the test drives that seam directly with the real plan-mode state machine.
    """
    from unittest.mock import MagicMock

    from openjiuwen.agent_teams.harness import TeamHarness
    from openjiuwen.agent_teams.schema.team import TeamRole
    from openjiuwen.harness.factory import create_deep_agent

    deep_agent = create_deep_agent(
        SimpleNamespace(model_client_config=None, model_config=None),
        card=AgentCard(name="team_leader", description="leader persona"),
        system_prompt="leader system prompt",
        workspace=str(tmp_path),
        enable_task_loop=True,
    )
    harness = TeamHarness(
        MagicMock(name="DeepAgentSpec"),
        None,
        deep_agent,
        role=TeamRole.LEADER,
        member_name="team_leader",
        initial_plan_mode=True,
    )
    session_id = f"team_plan_seed_{uuid.uuid4().hex}"
    team_session = create_agent_team_session(session_id=session_id)
    child = harness._make_child_session(team_session)
    await child.pre_run()

    harness._seed_initial_plan_mode(child)

    state = deep_agent.load_state(child)
    assert state.plan_mode.mode == "plan"
    assert state.plan_mode.plan_slug is None
    await isolated_checkpointer.release(session_id)


def test_team_harness_keeps_code_plan_prompt_when_not_team_plan(tmp_path):
    from unittest.mock import MagicMock

    from openjiuwen.agent_teams.harness import TeamHarness
    from openjiuwen.agent_teams.schema.team import TeamRole
    from openjiuwen.harness.factory import create_deep_agent
    from openjiuwen.harness.schema.config import SubAgentConfig
    from openjiuwen.harness.subagents.plan_agent import (
        PLAN_AGENT_DESC,
        PLAN_AGENT_SYSTEM_PROMPT_EN,
        build_plan_agent_config,
    )

    deep_agent = create_deep_agent(
        SimpleNamespace(model_client_config=None, model_config=None),
        card=AgentCard(name="team_leader", description="leader persona"),
        system_prompt="leader system prompt",
        workspace=str(tmp_path),
        subagents=[build_plan_agent_config(language="en")],
        language="en",
        enable_task_loop=True,
    )

    TeamHarness(
        MagicMock(name="DeepAgentSpec"),
        None,
        deep_agent,
        role=TeamRole.LEADER,
        member_name="team_leader",
        initial_plan_mode=False,
    )

    plan_spec = next(
        spec
        for spec in deep_agent.deep_config.subagents
        if isinstance(spec, SubAgentConfig) and spec.agent_card.name == "plan_agent"
    )
    assert plan_spec.agent_card.description == PLAN_AGENT_DESC["en"]
    assert plan_spec.system_prompt == PLAN_AGENT_SYSTEM_PROMPT_EN


@pytest.mark.asyncio
async def test_team_harness_seeds_team_plan_mode_only_once():
    """Initial plan-mode seeding is idempotent across run cycles.

    ``_seed_initial_plan_mode`` switches to plan mode the first time only;
    subsequent cycles (same harness instance) must not re-seed, preserving an
    existing plan slug and not forcing the leader back into plan mode.
    """
    from unittest.mock import MagicMock

    from openjiuwen.agent_teams.harness import TeamHarness
    from openjiuwen.agent_teams.schema.team import TeamRole

    state = SimpleNamespace(
        plan_mode=SimpleNamespace(mode="normal", plan_slug="existing-plan")
    )
    agent_session = SimpleNamespace()

    class FakeDeepAgent:
        card = AgentCard(name="team_leader", description="leader persona")

        def __init__(self):
            self.switch_modes = []

        def load_state(self, session):
            assert session is agent_session
            return state

        def switch_mode(self, session, mode):
            assert session is agent_session
            self.switch_modes.append(mode)
            state.plan_mode.mode = mode

    deep_agent = FakeDeepAgent()
    harness = TeamHarness(
        MagicMock(name="DeepAgentSpec"),
        None,
        deep_agent,
        role=TeamRole.LEADER,
        member_name="team_leader",
        initial_plan_mode=True,
    )

    # First cycle seeds plan mode; the slug is left untouched.
    harness._seed_initial_plan_mode(agent_session)
    assert deep_agent.switch_modes == ["plan"]
    assert state.plan_mode.plan_slug == "existing-plan"

    # A later cycle re-enters normal mode; seeding must not force plan again.
    state.plan_mode.mode = "normal"
    harness._seed_initial_plan_mode(agent_session)
    assert deep_agent.switch_modes == ["plan"]
    assert state.plan_mode.mode == "normal"


@pytest.mark.asyncio
async def test_team_harness_child_stream_close_does_not_close_team_stream(isolated_checkpointer):
    """The per-cycle child session is stream-isolated from the team session.

    ``_make_child_session`` derives the child with ``share_stream_writer=False``,
    so closing the child's stream (when the native cycle ends) must not close
    the team session's stream — the team stream stays open for the run loop.
    """
    from unittest.mock import MagicMock

    from openjiuwen.agent_teams.harness import TeamHarness
    from openjiuwen.agent_teams.schema.team import TeamRole

    deep_agent = SimpleNamespace(card=AgentCard(id="leader", name="leader"))
    harness = TeamHarness(
        MagicMock(name="DeepAgentSpec"),
        None,
        deep_agent,
        role=TeamRole.LEADER,
        member_name="leader",
    )
    session_id = f"team_stream_isolation_{uuid.uuid4().hex}"
    team_session = create_agent_team_session(session_id=session_id, team_id="stream_team")
    await team_session.pre_run(inputs={"query": "hello"})

    child = harness._make_child_session(team_session)
    await child.pre_run()
    # The native cycle would write + close the child stream at round/stop.
    await child.write_stream({"event_type": "child"})
    await child.close_stream()

    # The team stream is independent: still writable after the child closed.
    await team_session.write_stream({"event_type": "team_still_open"})
    await team_session.post_run()
    team_chunks = [chunk async for chunk in team_session.stream_iterator()]

    assert any(chunk.payload.get("event_type") == "team_still_open" for chunk in team_chunks)

    await isolated_checkpointer.release(session_id)


@pytest.mark.asyncio
async def test_agent_teams_session_does_not_inject_source_metadata(isolated_checkpointer):
    """AgentTeams uses TeamOutputSchema ownership fields instead of payload source metadata."""
    from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager

    session_id = f"agent_teams_no_source_meta_{uuid.uuid4().hex}"
    team_session = TeamRuntimeManager._build_session(session_id)
    await team_session.pre_run(inputs={"query": "hello"})

    child = team_session.create_agent_session(agent_id="leader")
    await child.pre_run(inputs={"payload": "child"})
    await child.write_stream({"kind": "child"})
    await child.post_run()

    await team_session.write_stream({"kind": "team"})
    await team_session.post_run()

    chunks = [chunk async for chunk in team_session.stream_iterator()]
    payloads = [chunk.payload for chunk in chunks if isinstance(getattr(chunk, "payload", None), dict)]

    assert payloads
    assert all("source_team_id" not in payload for payload in payloads)
    assert all("source_agent_id" not in payload for payload in payloads)

    await isolated_checkpointer.release(session_id)


@pytest.mark.asyncio
async def test_team_runtime_manager_cold_recover_reinjects_runtime_spec():
    from openjiuwen.agent_teams.runtime.dispatch import RunActionKind
    from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager

    session_id = f"cold_recover_{uuid.uuid4().hex}"
    spec = _runtime_spec("cold_recover_team")
    agent = FakeTeamAgent("cold_recover_team", stream_label="team.chunk")

    manager = TeamRuntimeManager()
    manager._pool.add = AsyncMock()

    with (
        patch.object(
            TeamRuntimeManager,
            "_inspect_session",
            AsyncMock(return_value=(True, True, None)),
        ),
        patch.object(
            TeamAgent,
            "recover_from_session",
            return_value=agent,
        ) as recover_mock,
    ):
        activation = await manager.activate(spec, session_id, {"query": "recover"})

    assert activation.action.kind is RunActionKind.COLD_RECOVER
    recover_mock.assert_called_once()
    args, kwargs = recover_mock.call_args
    # Call site: TeamAgent.recover_from_session(team_session, team_name, runtime_spec=spec)
    assert args[1] == "cold_recover_team"
    assert kwargs["runtime_spec"] is spec
    # COLD_RECOVER recovers only the leader here; teammate recovery is owned by
    # the leader's coordination.start (the activation is streamed right after),
    # so the manager no longer eagerly calls recover_team.
    assert agent.recover_calls == 0


@pytest.mark.asyncio
async def test_team_runtime_manager_recreates_pending_session_bucket():
    from openjiuwen.agent_teams.runtime.dispatch import RunActionKind
    from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
    from openjiuwen.agent_teams.runtime.metadata import TEAM_DB_STATE_PENDING_CREATE

    session_id = f"pending_recreate_{uuid.uuid4().hex}"
    team_name = "pending_recreate_team"
    spec = _runtime_spec(team_name)
    agent = FakeTeamAgent(team_name, stream_label="team.chunk")

    manager = TeamRuntimeManager()
    manager._pool.add = AsyncMock()

    with (
        patch.object(
            TeamRuntimeManager,
            "_inspect_session",
            AsyncMock(return_value=(True, False, TEAM_DB_STATE_PENDING_CREATE)),
        ),
        patch.object(TeamAgentSpec, "build", return_value=agent) as build_mock,
    ):
        activation = await manager.activate(spec, session_id, {"query": "create"})

    assert activation.action.kind is RunActionKind.CREATE
    build_mock.assert_called_once()
    manager._pool.add.assert_awaited_once()


@pytest.mark.asyncio
async def test_activate_waits_for_in_flight_pause_then_resumes(isolated_checkpointer):
    """activate 撞 PAUSING 条目应等 pause 落定再 resume（不误拒）。

    触发缝（流末清注册 × 池仍 RUNNING）在 pause 慢/锁库下是分钟级误拒窗；
    修复后撞窗请求等 pause 完成 → RESUME_FROM_PAUSE。
    """
    import asyncio as _asyncio

    from openjiuwen.agent_teams.runtime.dispatch import RunActionKind
    from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
    from openjiuwen.agent_teams.runtime.pool import RuntimeState

    session_id = f"pausing_{uuid.uuid4().hex}"
    spec = _runtime_spec("pausing_team")
    agent = FakeTeamAgent("pausing_team", stream_label="team.chunk")

    manager = TeamRuntimeManager()
    await _activate_pool_entry(manager, "pausing_team", session_id, agent)
    entry = await manager.pool.get("pausing_team")
    entry.state = RuntimeState.PAUSING
    entry.pause_done.clear()

    async def _finish_pause() -> None:
        await _asyncio.sleep(0.05)
        entry.state = RuntimeState.PAUSED
        entry.pause_done.set()

    finisher = _asyncio.create_task(_finish_pause())
    with patch.object(
            TeamRuntimeManager,
            "_inspect_session",
            AsyncMock(return_value=(True, True, None)),
    ):
        activation = await manager.activate(spec, session_id, {"query": "follow up"})
    await finisher

    assert activation.action.kind is RunActionKind.RESUME_FROM_PAUSE
    assert entry.state == RuntimeState.RUNNING  # resume 翻转回 RUNNING


@pytest.mark.asyncio
async def test_activate_pausing_timeout_falls_back_to_reject(isolated_checkpointer):
    """超时兜底：pause 超过预算未落定 → 按 RUNNING 拒绝
    （上层零产出守卫拆除重建自愈）。"""
    from openjiuwen.agent_teams.runtime.dispatch import RunActionKind
    from openjiuwen.agent_teams.runtime import manager as manager_mod
    from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
    from openjiuwen.agent_teams.runtime.pool import RuntimeState

    session_id = f"pausing_timeout_{uuid.uuid4().hex}"
    spec = _runtime_spec("pausing_timeout_team")
    agent = FakeTeamAgent("pausing_timeout_team", stream_label="team.chunk")

    manager = TeamRuntimeManager()
    await _activate_pool_entry(manager, "pausing_timeout_team", session_id, agent)
    entry = await manager.pool.get("pausing_timeout_team")
    entry.state = RuntimeState.PAUSING
    entry.pause_done.clear()

    with (
        patch.object(
            TeamRuntimeManager,
            "_inspect_session",
            AsyncMock(return_value=(True, True, None)),
        ),
        patch.object(manager_mod, "_PAUSING_WAIT_TIMEOUT_SEC", 0.01),
    ):
        activation = await manager.activate(spec, session_id, {"query": "follow up"})

    assert activation.action.kind is RunActionKind.REJECT_RUNNING
    assert "pause in progress" in (activation.action.reason or "")
    # 拒绝是零副作用：条目仍在、状态未被改动
    assert entry.state == RuntimeState.PAUSING


@pytest.mark.asyncio
async def test_pause_cancelled_midflight_rolls_back_to_running(isolated_checkpointer):
    """pause 协程被取消（CancelledError 是 BaseException）不得把条目卡在 PAUSING。

    修复前：state 只在 pause 成功后翻 PAUSED，取消穿透 except Exception →
    永久 PAUSING（dispatch 恒拒、pause 报假成功、finalize 永远跳过）。
    """
    import asyncio as _asyncio

    from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
    from openjiuwen.agent_teams.runtime.pool import RuntimeState

    session_id = f"pause_cancel_{uuid.uuid4().hex}"

    class _HangingPauseAgent(FakeTeamAgent):
        def __init__(self, team_name: str):
            super().__init__(team_name, stream_label="hang.chunk")
            self.pause_started = _asyncio.Event()

        async def pause_coordination(self) -> None:
            self.pause_started.set()
            await _asyncio.sleep(3600)

    agent = _HangingPauseAgent("cancel_team")
    manager = TeamRuntimeManager()
    await _activate_pool_entry(manager, "cancel_team", session_id, agent)
    entry = await manager.pool.get("cancel_team")

    pause_task = _asyncio.create_task(
        manager.pause(team_name="cancel_team", session_id=session_id)
    )
    await agent.pause_started.wait()
    assert entry.state == RuntimeState.PAUSING
    pause_task.cancel()
    with pytest.raises(_asyncio.CancelledError):
        await pause_task
    assert entry.state == RuntimeState.RUNNING, "取消路径也必须把状态翻回 RUNNING"
    assert entry.pause_done.is_set(), "取消路径同样要释放 pause 等待者"


@pytest.mark.asyncio
async def test_pause_on_paused_entry_is_noop_success(isolated_checkpointer):
    """对已 PAUSED 条目 pause = no-op success（docstring 承诺）：不得重跑
    pause_coordination——否则失败回滚会把本已 PAUSED 的条目翻成 RUNNING
    （状态倒退，dispatch 侧表现为假复活）。"""
    from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
    from openjiuwen.agent_teams.runtime.pool import RuntimeState

    session_id = f"pause_paused_{uuid.uuid4().hex}"

    class _FailingPauseAgent(FakeTeamAgent):
        async def pause_coordination(self) -> None:
            raise RuntimeError("must not be called on an already-PAUSED entry")

    agent = _FailingPauseAgent("paused_team", stream_label="paused.chunk")
    manager = TeamRuntimeManager()
    await _activate_pool_entry(manager, "paused_team", session_id, agent)
    entry = await manager.pool.get("paused_team")
    entry.state = RuntimeState.PAUSED

    result = await manager.pause(team_name="paused_team", session_id=session_id)
    assert result is True, "已 PAUSED 条目幂等报成功"
    assert entry.state == RuntimeState.PAUSED, "已 PAUSED 条目不得被翻态"


@pytest.mark.asyncio
async def test_finalize_cancelled_mid_pause_rolls_back_to_running(isolated_checkpointer):
    """finalize 驱动的 pause 被取消同样回 RUNNING（与显式 pause 同规）。"""
    import asyncio as _asyncio

    from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
    from openjiuwen.agent_teams.runtime.pool import RuntimeState

    session_id = f"fin_cancel_{uuid.uuid4().hex}"

    class _HangingPauseAgent(FakeTeamAgent):
        lifecycle = "persistent"

        def __init__(self, team_name: str):
            super().__init__(team_name, stream_label="hang.chunk")
            self.pause_started = _asyncio.Event()

        async def is_shutdown_requested(self) -> bool:
            return False

        async def pause_coordination(self) -> None:
            self.pause_started.set()
            await _asyncio.sleep(3600)

    agent = _HangingPauseAgent("fin_cancel_team")
    manager = TeamRuntimeManager()
    await _activate_pool_entry(manager, "fin_cancel_team", session_id, agent)
    entry = await manager.pool.get("fin_cancel_team")

    finalize_task = _asyncio.create_task(
        manager.finalize(team_name="fin_cancel_team", session_id=session_id)
    )
    await agent.pause_started.wait()
    assert entry.state == RuntimeState.PAUSING
    finalize_task.cancel()
    with pytest.raises(_asyncio.CancelledError):
        await finalize_task
    assert entry.state == RuntimeState.RUNNING
    assert entry.pause_done.is_set()


@pytest.mark.asyncio
async def test_activate_pause_wait_refetch_cross_session_entry_torn_down(isolated_checkpointer):
    """等 pause 落定期间另一条会话抢走池条目：重取后必须识别跨会话并拆掉，
    不得原样交给 dispatch（那里是 invariant 违例直接 RuntimeError）。"""
    import asyncio as _asyncio

    from openjiuwen.agent_teams.runtime.dispatch import RunActionKind
    from openjiuwen.agent_teams.runtime.manager import (
        TeamRuntimeActivation,
        TeamRuntimeManager,
    )
    from openjiuwen.agent_teams.runtime.pool import ActiveTeam, RuntimeState

    session_a = f"swap_a_{uuid.uuid4().hex}"
    session_b = f"swap_b_{uuid.uuid4().hex}"
    spec = _runtime_spec("swap_team")
    agent_a = FakeTeamAgent("swap_team", stream_label="a.chunk")
    agent_b = FakeTeamAgent("swap_team", stream_label="b.chunk")

    manager = TeamRuntimeManager()
    await _activate_pool_entry(manager, "swap_team", session_a, agent_a)
    entry_a = await manager.pool.get("swap_team")
    entry_a.state = RuntimeState.PAUSING
    entry_a.pause_done.clear()

    async def _swap_to_session_b() -> None:
        await _asyncio.sleep(0.05)
        # 另一会话的 activate：拆掉 PAUSING 旧条目，换上绑定 session_b 的新条目
        await manager.pool.remove("swap_team")
        await manager.pool.add(
            ActiveTeam(
                team_name="swap_team",
                agent=agent_b,
                current_session_id=session_b,
                state=RuntimeState.RUNNING,
            )
        )
        entry_a.pause_done.set()

    swapper = _asyncio.create_task(_swap_to_session_b())
    captured: dict[str, Any] = {}

    async def _fake_apply(self, action, **kwargs):
        captured["pool_entry"] = kwargs.get("pool_entry")
        return TeamRuntimeActivation(
            agent=None,
            session=kwargs["team_session"],
            action=action,
        )

    with (
        patch.object(
            TeamRuntimeManager,
            "_inspect_session",
            AsyncMock(return_value=(True, True, None)),
        ),
        patch.object(TeamRuntimeManager, "_apply_action", _fake_apply),
        patch.object(
            TeamRuntimeManager, "stop_team", AsyncMock()
        ) as stop_mock,
    ):
        activation = await manager.activate(spec, session_a, {"query": "follow up"})
    await swapper

    stop_mock.assert_awaited_once_with(team_name="swap_team", session_id=session_b)
    assert captured["pool_entry"] is None, "跨会话条目拆除后必须按无条目冷路径 dispatch"
    assert activation.action.kind is RunActionKind.COLD_RECOVER


@pytest.mark.asyncio
async def test_pause_on_pausing_entry_awaits_and_reports_truthfully(isolated_checkpointer):
    """PAUSING 条目的重复 pause：等在飞的落定后如实报告结果——
    在飞成功（PAUSED）→ True；在飞失败（回 RUNNING）→ False。
    不得「报成功但从未 pause」（假成功）。"""
    import asyncio as _asyncio

    from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
    from openjiuwen.agent_teams.runtime.pool import RuntimeState

    session_id = f"pause_dup_{uuid.uuid4().hex}"
    agent = FakeTeamAgent("dup_pause_team", stream_label="x.chunk")
    manager = TeamRuntimeManager()
    await _activate_pool_entry(manager, "dup_pause_team", session_id, agent)
    entry = await manager.pool.get("dup_pause_team")
    entry.state = RuntimeState.PAUSING
    entry.pause_done.clear()

    # 在飞 pause 成功落定 → 重复 pause 等落定后报 True
    async def _settle_ok() -> None:
        await _asyncio.sleep(0.05)
        entry.state = RuntimeState.PAUSED
        entry.pause_done.set()

    settle = _asyncio.create_task(_settle_ok())
    assert await manager.pause(team_name="dup_pause_team", session_id=session_id) is True
    await settle
    assert agent.pause_calls == 0, "重复 pause 不得叠加触发 pause_coordination"

    # 在飞 pause 失败回 RUNNING → 如实 False（调用方知道没停住）
    entry.state = RuntimeState.PAUSING
    entry.pause_done.clear()

    async def _settle_fail() -> None:
        await _asyncio.sleep(0.05)
        entry.state = RuntimeState.RUNNING
        entry.pause_done.set()

    settle2 = _asyncio.create_task(_settle_fail())
    assert await manager.pause(team_name="dup_pause_team", session_id=session_id) is False
    await settle2


@pytest.mark.asyncio
async def test_finalize_pausing_wait_still_makes_stop_decision(isolated_checkpointer):
    """finalize 撞 PAUSING：等在飞落定后仍须做 stop/pause 决策——
    直接 return 会把 shutdown_requested/非持久团队泄漏为 PAUSED 挂池；
    落定 PAUSED 且决策也是 pause 时幂等跳过（不重复 pause）。"""
    import asyncio as _asyncio

    from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
    from openjiuwen.agent_teams.runtime.pool import RuntimeState

    # 形态一：在飞 pause 落定 PAUSED + shutdown_requested → 必须 stop 拆条目
    class _ShutdownAgent(FakeTeamAgent):
        lifecycle = "persistent"

        async def is_shutdown_requested(self) -> bool:
            return True

    session_a = f"fin_stop_{uuid.uuid4().hex}"
    agent = _ShutdownAgent("fin_wait_team", stream_label="x.chunk")
    manager = TeamRuntimeManager()
    await _activate_pool_entry(manager, "fin_wait_team", session_a, agent)
    entry = await manager.pool.get("fin_wait_team")
    entry.state = RuntimeState.PAUSING
    entry.pause_done.clear()

    async def _settle_paused() -> None:
        await _asyncio.sleep(0.05)
        entry.state = RuntimeState.PAUSED
        entry.pause_done.set()

    settle = _asyncio.create_task(_settle_paused())
    await manager.finalize(team_name="fin_wait_team", session_id=session_a)
    await settle
    assert agent.stop_calls == 1, "shutdown_requested 必须 stop（即使撞过 PAUSING）"
    assert await manager.pool.get("fin_wait_team") is None

    # 形态二：在飞落定 PAUSED + 决策也是 pause → 幂等跳过（不重复 pause_coordination）
    class _PersistentAgent(FakeTeamAgent):
        lifecycle = "persistent"

        async def is_shutdown_requested(self) -> bool:
            return False

    session_b = f"fin_keep_{uuid.uuid4().hex}"
    agent2 = _PersistentAgent("fin_keep_team", stream_label="x.chunk")
    manager2 = TeamRuntimeManager()
    await _activate_pool_entry(manager2, "fin_keep_team", session_b, agent2)
    entry2 = await manager2.pool.get("fin_keep_team")
    entry2.state = RuntimeState.PAUSING
    entry2.pause_done.clear()

    async def _settle_paused2() -> None:
        await _asyncio.sleep(0.05)
        entry2.state = RuntimeState.PAUSED
        entry2.pause_done.set()

    settle_b = _asyncio.create_task(_settle_paused2())
    await manager2.finalize(team_name="fin_keep_team", session_id=session_b)
    await settle_b
    assert agent2.pause_calls == 0, "已 PAUSED 不得重复 pause"
    assert agent2.stop_calls == 0
    assert (await manager2.pool.get("fin_keep_team")).state == RuntimeState.PAUSED


@pytest.mark.asyncio
async def test_finalize_rechecks_pausing_flipped_during_shutdown_probe(isolated_checkpointer):
    """finalize 的 is_shutdown_requested await 窗口内被外部 pause() 抢跑：
    必须回到等落定分支重决策——不得带旧视图进 else 分支。

    旧实现的后果链：抢写 pause_done（抹掉在飞 pause 的所有权标记）→ 与
    在飞 pause 并发 pause_coordination → 自己 finally 抢先写 PAUSED +
    pause_done.set()，等待方把条目当 PAUSED resume，与仍在收尾的 pause
    冲突（「已 resume 但 pause 仍在跑」状态机错乱）。本用例在旧实现上
    必红：finalize 会自己再跑一次 pause_coordination（pause_calls=1）。
    """
    import asyncio as _asyncio

    from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
    from openjiuwen.agent_teams.runtime.pool import RuntimeState

    class _FlipPausingAgent(FakeTeamAgent):
        """首次 is_shutdown_requested 探测期间模拟外部 pause() 抢跑。"""

        lifecycle = "persistent"

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.shutdown_probe_calls = 0
            self.pool_entry = None

        async def is_shutdown_requested(self) -> bool:
            self.shutdown_probe_calls += 1
            if self.shutdown_probe_calls == 1:
                self.pool_entry.state = RuntimeState.PAUSING
                self.pool_entry.pause_done.clear()

                async def _settle() -> None:
                    await _asyncio.sleep(0.05)
                    self.pool_entry.state = RuntimeState.PAUSED
                    self.pool_entry.pause_done.set()

                _asyncio.create_task(_settle())
            return False

    # 形态一：抢跑落定 PAUSED + 重决策仍是 pause → 幂等跳过，finalize
    # 自己不得再跑 pause_coordination（重复 pause）
    session_a = f"fin_flip_{uuid.uuid4().hex}"
    agent = _FlipPausingAgent("fin_flip_team", stream_label="x.chunk")
    manager = TeamRuntimeManager()
    await _activate_pool_entry(manager, "fin_flip_team", session_a, agent)
    agent.pool_entry = await manager.pool.get("fin_flip_team")

    await manager.finalize(team_name="fin_flip_team", session_id=session_a)
    entry = await manager.pool.get("fin_flip_team")
    assert entry is not None and entry.state == RuntimeState.PAUSED
    assert agent.pause_calls == 0, "撞在飞 pause 后 finalize 不得再跑 pause_coordination"
    assert agent.shutdown_probe_calls == 2, "落定后必须重取条目重决策（作废旧读数）"
    assert agent.stop_calls == 0

    # 形态二：抢跑落定后重决策读到 shutdown_requested=True → 仍须 stop 拆
    # 条目（不得因在飞 pause 跳过 stop 决策把条目泄漏为 PAUSED 挂池）
    class _FlipThenShutdownAgent(_FlipPausingAgent):
        async def is_shutdown_requested(self) -> bool:
            await super().is_shutdown_requested()
            return self.shutdown_probe_calls >= 2

    session_b = f"fin_flip_stop_{uuid.uuid4().hex}"
    agent2 = _FlipThenShutdownAgent("fin_flip_stop_team", stream_label="x.chunk")
    manager2 = TeamRuntimeManager()
    await _activate_pool_entry(manager2, "fin_flip_stop_team", session_b, agent2)
    agent2.pool_entry = await manager2.pool.get("fin_flip_stop_team")

    await manager2.finalize(team_name="fin_flip_stop_team", session_id=session_b)
    assert agent2.stop_calls == 1, "重决策读到 shutdown_requested 必须 stop"
    assert await manager2.pool.get("fin_flip_stop_team") is None
    assert agent2.shutdown_probe_calls == 2


def test_runner_get_team_runtime_manager_classmethod_facade():
    """Runner.get_team_runtime_manager() 类门面可达且与单例内部一致。

    Runner 只 mixin _TeamRunnerClassMixin（不含 _TeamRunnerMixin）——没有
    门面时 embedder 按惯例写 Runner.get_team_runtime_manager() 直接
    AttributeError（旧实现本用例必红），只能绕道私有的
    _global_runner()._get_team_runtime_manager()。
    """
    from openjiuwen.core.runner.team_runner import _global_runner

    manager = Runner.get_team_runtime_manager()
    assert manager is _global_runner()._get_team_runtime_manager()
    assert Runner.get_team_runtime_manager() is manager  # 同一单例


@pytest.mark.asyncio
async def test_runner_session_switch_stops_and_rebuilds(
        isolated_checkpointer,
        stateful_team_db,
):
    """Switching sessions for the same team tears down the pool entry
    (stop_coordination + pool.remove) before redispatching cold.

    Replaces the old warm-session-switch path (WARM_RECOVER /
    NEW_TEAM_IN_SESSION_WARM) which reused the same TeamAgent instance
    across sessions and was prone to state bleed. The new contract:
    every cross-session activation starts from a freshly built or
    cold-recovered agent against an empty pool slot.
    """
    await Runner.start()
    session_one = f"resume_{uuid.uuid4().hex}"
    session_two = f"resume_{uuid.uuid4().hex}"
    spec = _runtime_spec("persistent_team")
    active_agent = FakeTeamAgent("persistent_team", stream_label="active.chunk")

    with patch.object(TeamAgentSpec, "build", return_value=active_agent):
        first_chunks = [
            chunk
            async for chunk in Runner.run_agent_team_streaming(
                agent_team=spec,
                inputs={"query": "first"},
                session=session_one,
            )
        ]
        # Stand in for BuildTeamTool persisting the team_info row during
        # round 1; subsequent dispatch reads team_exists from the DB.
        await stateful_team_db.team.create_team(
            team_name="persistent_team",
            display_name="persistent_team",
            leader_member_name="leader",
        )
        second_chunks = [
            chunk
            async for chunk in Runner.run_agent_team_streaming(
                agent_team=spec,
                inputs={"query": "second"},
                session=session_two,
            )
        ]

    assert first_chunks[0].payload["activation_kind"] == "create"
    # session_two has no bucket for the team yet (round 1 wrote into
    # session_one). The stale session_one pool entry is torn down before
    # dispatch, so the cold path picks NEW_TEAM_IN_SESSION (team_in_db
    # True via the manual create_team above; team_in_session False).
    assert second_chunks[0].payload["activation_kind"] == "new_team_in_session"
    assert active_agent.resume_calls == [session_two]
    # The pool teardown on session switch must have called stop_coordination
    # at least once on the round-one agent (session_one -> session_two).
    assert active_agent.stop_calls >= 1

    await Runner.stop()
    await isolated_checkpointer.release(session_one)
    await isolated_checkpointer.release(session_two)


@pytest.mark.asyncio
async def test_runner_same_session_streaming_short_circuits_and_skips_second_stream(isolated_checkpointer):
    session_id = f"same_{uuid.uuid4().hex}"
    spec = _runtime_spec("same_team")
    agent = FakeTeamAgent("same_team", stream_label="active.chunk")
    await Runner.start()

    with patch.object(TeamAgentSpec, "build", return_value=agent):
        first_chunks = [
            chunk
            async for chunk in Runner.run_agent_team_streaming(
                agent_team=spec,
                inputs={"query": "first"},
                session=session_id,
            )
        ]
        second_chunks = [
            chunk
            async for chunk in Runner.run_agent_team_streaming(
                agent_team=spec,
                inputs={"query": "same-session"},
                session=session_id,
            )
        ]

    assert first_chunks[0].payload["activation_kind"] == "create"
    # Same (team, session) running -> dispatch rejects with reject_running.
    assert second_chunks == []
    assert agent.stream_calls == 1
    assert agent.invoke_calls == 1
    assert agent.resume_calls == []

    await Runner.stop()
    await isolated_checkpointer.release(session_id)


@pytest.mark.asyncio
async def test_runner_reject_running_does_not_finalize_healthy_team(isolated_checkpointer):
    """REJECT 类调用零副作用——不得把健康在跑的团队误 pause。

    修复前：被拒调用的 finally 无条件 finalize → 池条目被翻成 PAUSED
    （锁库/慢写下还会拖出分钟级连锁故障）。修复后：池条目保持 RUNNING。
    """
    from openjiuwen.agent_teams.runtime.pool import RuntimeState
    from openjiuwen.core.runner.team_runner import _global_runner

    session_id = f"reject_{uuid.uuid4().hex}"
    spec = _runtime_spec("reject_team")
    agent = FakeTeamAgent("reject_team", stream_label="active.chunk")
    await Runner.start()
    try:
        with patch.object(TeamAgentSpec, "build", return_value=agent):
            first_chunks = [
                chunk
                async for chunk in Runner.run_agent_team_streaming(
                    agent_team=spec,
                    inputs={"query": "first"},
                    session=session_id,
                )
            ]
            assert first_chunks[0].payload["activation_kind"] == "create"
            manager = _global_runner()._get_team_runtime_manager()
            entry = await manager.pool.get("reject_team")
            assert entry is not None and entry.state == RuntimeState.RUNNING

            second_chunks = [
                chunk
                async for chunk in Runner.run_agent_team_streaming(
                    agent_team=spec,
                    inputs={"query": "same-session"},
                    session=session_id,
                )
            ]
            assert second_chunks == []  # reject_running 空流
            entry = await manager.pool.get("reject_team")
            assert entry is not None, "reject 不得移除池条目"
            assert entry.state == RuntimeState.RUNNING, (
                "reject 不得 finalize（误 pause 健康团队）"
            )
    finally:
        await Runner.stop()
        await isolated_checkpointer.release(session_id)


@pytest.mark.asyncio
async def test_runner_reject_closes_stream_without_committing_checkpoint(isolated_checkpointer):
    """REJECT 路径只关自己的 stream emitter、绝不 commit checkpoint
    （零持久化副作用）。

    被拒调用的 session 是 _build_session 新建对象，其状态 = pre_run
    （_inspect_session 无条件跑过）时刻从 checkpoint 恢复的旧快照，与在跑
    团队共享 session_id；checkpointer save 是全量 last-writer-wins，而在
    跑团队回合内持续 flush_checkpoint——reject 若 commit，旧快照会覆盖
    较新状态、丢团队进度。（旧实现 reject 走 post_run 内含 commit：本
    用例在旧实现上必红，commit_calls 会随每次 reject +1。）
    """
    from openjiuwen.core.session.agent_team import Session as TeamSessionWrapper

    session_id = f"reject_nocommit_{uuid.uuid4().hex}"
    spec = _runtime_spec("reject_nocommit_team")
    agent = FakeTeamAgent("reject_nocommit_team", stream_label="active.chunk")
    await Runner.start()
    try:
        real_close_stream = TeamSessionWrapper.close_stream
        real_commit = TeamSessionWrapper.commit
        close_stream_calls = 0
        commit_calls = 0

        async def _counting_close_stream(self):
            nonlocal close_stream_calls
            close_stream_calls += 1
            await real_close_stream(self)

        async def _counting_commit(self):
            nonlocal commit_calls
            commit_calls += 1
            await real_commit(self)

        with (
            patch.object(TeamAgentSpec, "build", return_value=agent),
            patch.object(TeamSessionWrapper, "close_stream", _counting_close_stream),
            patch.object(TeamSessionWrapper, "commit", _counting_commit),
        ):
            first_chunks = [
                chunk
                async for chunk in Runner.run_agent_team_streaming(
                    agent_team=spec,
                    inputs={"query": "first"},
                    session=session_id,
                )
            ]
            assert first_chunks[0].payload["activation_kind"] == "create"
            # 正常轮收尾 = post_run（close_stream+commit），另有 activate 期
            # _flush_team_manifest 的一次 commit——不钉绝对值，记基线。
            baseline_close = close_stream_calls
            baseline_commit = commit_calls

            rejected_chunks = [
                chunk
                async for chunk in Runner.run_agent_team_streaming(
                    agent_team=spec,
                    inputs={"query": "same-session"},
                    session=session_id,
                )
            ]
            assert rejected_chunks == []  # reject_running 空流
            assert close_stream_calls == baseline_close + 1, (
                "reject 仍须关闭自己 session 的 stream emitter（只开不收会泄漏）"
            )
            assert commit_calls == baseline_commit, (
                "reject 不得 commit——旧快照会 last-writer-wins 覆盖在跑团队的新状态"
            )

            result = await Runner.run_agent_team(
                agent_team=spec,
                inputs={"query": "same-session invoke"},
                session=session_id,
            )
            assert result is None  # invoke 路径同被拒
            assert close_stream_calls == baseline_close + 2, "invoke 的 reject 路径同规"
            assert commit_calls == baseline_commit, "invoke 的 reject 路径同规"
    finally:
        await Runner.stop()
        await isolated_checkpointer.release(session_id)


@pytest.mark.asyncio
async def test_stream_cleanup_gate_failure_does_not_swallow_finalize_and_post_run(
        isolated_checkpointer,
        stateful_team_db,
):
    """流式收尾三步各自独立兜底：interact gate 关闭失败不得吞掉 finalize
    （pause/stop 决策）与 post_run——否则池条目滞留 RUNNING，下一轮
    activate 被误 REJECT。"""
    from openjiuwen.agent_teams.runtime.gate import InteractGate
    from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
    from openjiuwen.core.session.agent_team import Session as TeamSessionWrapper

    session_id = f"cleanup_iso_{uuid.uuid4().hex}"
    spec = _runtime_spec("cleanup_iso_team")
    agent = FakeTeamAgent("cleanup_iso_team", stream_label="team.chunk")
    await Runner.start()
    try:
        real_post_run = TeamSessionWrapper.post_run
        post_run_calls = 0

        async def _counting_post_run(self):
            nonlocal post_run_calls
            post_run_calls += 1
            await real_post_run(self)

        finalize_calls = []

        async def _spy_finalize(self, *, team_name, session_id):
            finalize_calls.append((team_name, session_id))

        async def _failing_close_and_drain(self):
            raise RuntimeError("gate close boom")

        with (
            patch.object(TeamAgentSpec, "build", return_value=agent),
            patch.object(TeamSessionWrapper, "post_run", _counting_post_run),
            patch.object(TeamRuntimeManager, "finalize", _spy_finalize),
            patch.object(InteractGate, "close_and_drain", _failing_close_and_drain),
        ):
            chunks = [
                chunk
                async for chunk in Runner.run_agent_team_streaming(
                    agent_team=spec,
                    inputs={"query": "hello"},
                    session=session_id,
                )
            ]
            assert len(chunks) == 2, "gate 关闭失败不得影响已产出的流帧"
            assert finalize_calls == [("cleanup_iso_team", session_id)], (
                "gate 关闭失败不得吞掉 finalize"
            )
            assert post_run_calls == 1, "gate 关闭失败不得吞掉 post_run"
    finally:
        await Runner.stop()
        await isolated_checkpointer.release(session_id)


@pytest.mark.asyncio
async def test_invoke_cleanup_gate_failure_does_not_swallow_finalize_and_post_run(
        isolated_checkpointer,
        stateful_team_db,
):
    """invoke 路径与流式同规：收尾三步各自独立兜底，interact gate 关闭失败
    不得吞掉 finalize（pause/stop 决策）与 post_run。"""
    from openjiuwen.agent_teams.runtime.gate import InteractGate
    from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
    from openjiuwen.core.session.agent_team import Session as TeamSessionWrapper

    session_id = f"cleanup_invoke_{uuid.uuid4().hex}"
    spec = _runtime_spec("cleanup_invoke_team")
    agent = FakeTeamAgent("cleanup_invoke_team", stream_label="team.chunk")
    await Runner.start()
    try:
        real_post_run = TeamSessionWrapper.post_run
        post_run_calls = 0

        async def _counting_post_run(self):
            nonlocal post_run_calls
            post_run_calls += 1
            await real_post_run(self)

        finalize_calls = []

        async def _spy_finalize(self, *, team_name, session_id):
            finalize_calls.append((team_name, session_id))

        async def _failing_close_and_drain(self):
            raise RuntimeError("gate close boom")

        with (
            patch.object(TeamAgentSpec, "build", return_value=agent),
            patch.object(TeamSessionWrapper, "post_run", _counting_post_run),
            patch.object(TeamRuntimeManager, "finalize", _spy_finalize),
            patch.object(InteractGate, "close_and_drain", _failing_close_and_drain),
        ):
            result = await Runner.run_agent_team(
                agent_team=spec,
                inputs={"query": "hello"},
                session=session_id,
            )
            assert result is not None, "gate 关闭失败不得影响 invoke 返回"
            assert finalize_calls == [("cleanup_invoke_team", session_id)], (
                "gate 关闭失败不得吞掉 finalize"
            )
            assert post_run_calls == 1, "gate 关闭失败不得吞掉 post_run"
    finally:
        await Runner.stop()
        await isolated_checkpointer.release(session_id)


@pytest.mark.asyncio
async def test_runner_same_session_after_pause_resumes_paused_runtime(
        isolated_checkpointer,
        stateful_team_db,
):
    session_id = f"paused_{uuid.uuid4().hex}"
    spec = _runtime_spec("paused_team")
    agent = FakeTeamAgent("paused_team", stream_label="team.chunk")
    await Runner.start()

    with patch.object(TeamAgentSpec, "build", return_value=agent):
        first_chunks = [
            chunk
            async for chunk in Runner.run_agent_team_streaming(
                agent_team=spec,
                inputs={"query": "first"},
                session=session_id,
            )
        ]
        await stateful_team_db.team.create_team(
            team_name="paused_team",
            display_name="paused_team",
            leader_member_name="leader",
        )
        assert await Runner.pause_agent_team(team_name="paused_team", session_id=session_id) is True

        second_chunks = [
            chunk
            async for chunk in Runner.run_agent_team_streaming(
                agent_team=spec,
                inputs={"query": "resume paused"},
                session=session_id,
            )
        ]

    assert first_chunks[0].payload["activation_kind"] == "create"
    assert second_chunks[0].payload["activation_kind"] == "resume_from_pause"
    assert second_chunks[1].payload["event_type"] == "team.chunk"
    assert agent.pause_calls == 1
    assert agent.stream_calls == 2
    assert agent.invoke_calls == 2

    await Runner.stop()
    await isolated_checkpointer.release(session_id)


@pytest.mark.asyncio
async def test_runner_paused_same_session_resume_uses_same_prepared_session(
        isolated_checkpointer,
        stateful_team_db,
):
    session_id = f"paused_same_{uuid.uuid4().hex}"
    spec = _runtime_spec("paused_same_team")
    agent = FakeTeamAgent("paused_same_team", stream_label="team.chunk")
    await Runner.start()

    with patch.object(TeamAgentSpec, "build", return_value=agent):
        initial_chunks = [
            chunk
            async for chunk in Runner.run_agent_team_streaming(
                agent_team=spec,
                inputs={"query": "first"},
                session=session_id,
            )
        ]
        assert initial_chunks[0].payload["activation_kind"] == "create"
        await stateful_team_db.team.create_team(
            team_name="paused_same_team",
            display_name="paused_same_team",
            leader_member_name="leader",
        )
        assert await Runner.pause_agent_team(team_name="paused_same_team", session_id=session_id) is True

        resumed_result = await Runner.run_agent_team(
            agent_team=spec,
            inputs={"query": "resume invoke"},
            session=session_id,
        )

    assert resumed_result["session_id"] == session_id
    assert agent.invoke_calls == 2
    assert agent.pause_calls == 1

    await Runner.stop()
    await isolated_checkpointer.release(session_id)


@pytest.mark.asyncio
async def test_runner_interact_pause_and_delete_agent_team_route_through_team_runtime_manager(isolated_checkpointer):
    await Runner.start()
    session_id = f"delete_{uuid.uuid4().hex}"
    spec = _runtime_spec("delete_team")
    agent = FakeTeamAgent("delete_team", stream_label="team.chunk")

    fake_db = AsyncMock()
    fake_db.initialize = AsyncMock()
    fake_db.drop_session_tables_by_id = AsyncMock(return_value=["team_task_xxx"])
    fake_db.team = AsyncMock()
    fake_db.team.team_exists = AsyncMock(return_value=False)
    fake_db.team.delete_team = AsyncMock(return_value=True)

    with (
        patch.object(TeamAgentSpec, "build", return_value=agent),
        patch("openjiuwen.agent_teams.spawn.shared_resources.get_shared_db", return_value=fake_db),
    ):
        async for _ in Runner.run_agent_team_streaming(
                agent_team=spec,
                inputs={"query": "hello"},
                session=session_id,
        ):
            pass

        # The stream has ended, so the InteractGate is closed: late
        # interact_team calls must be rejected with gate_closed rather
        # than reaching the agent.
        post_stream_result = await Runner.interact_agent_team(
            "follow-up",
            team_name="delete_team",
            session_id=session_id,
        )
        assert post_stream_result.ok is False
        assert post_stream_result.reason == "gate_closed"
        assert agent.interactions == []

        assert await Runner.pause_agent_team(team_name="delete_team", session_id=session_id) is True
        assert agent.pause_calls == 1

        # Static precondition: paused runtime still occupies the pool, so
        # the team must be stopped before delete is allowed.
        assert await Runner.stop_agent_team(team_name="delete_team", session_id=session_id) is True

        assert await Runner.delete_agent_team(team_name="delete_team", session_ids=[session_id]) is True
        # ``initialize`` is also called by the dispatch DB-existence probe
        # in ``_inspect_session``, so assert it ran at least once instead of
        # exactly once.
        fake_db.initialize.assert_awaited()
        fake_db.drop_session_tables_by_id.assert_awaited_once_with(session_id)
        fake_db.team.delete_team.assert_awaited_once_with("delete_team")

    assert await isolated_checkpointer.session_exists(session_id) is False

    await Runner.stop()


@pytest.mark.asyncio
async def test_team_agent_resume_for_new_session_rebinds_only_live_teammates():
    leader_card = AgentCard(id=f"leader_{uuid.uuid4().hex}", name="leader", description="leader")
    agent = TeamAgent(card=leader_card)
    team_spec = TeamSpec(team_name="persistent_team", display_name="persistent_team")
    ctx = TeamRuntimeContext(role=TeamRole.LEADER, member_name="leader", team_spec=team_spec)
    agent._configurator._blueprint = TeamAgentBlueprint(
        card=leader_card,
        spec=TeamAgentSpec(agents={"leader": DeepAgentSpec()}, team_name="persistent_team"),
        ctx=ctx,
        language="en",
    )

    fake_db = AsyncMock()
    fake_backend = SimpleNamespace(
        team_name="persistent_team",
        db=fake_db,
        list_member_roster=AsyncMock(
            return_value=[
                SimpleNamespace(member_name="leader", status="ready"),
                SimpleNamespace(member_name="worker_busy", status="busy"),
                SimpleNamespace(member_name="worker_ready", status="ready"),
                SimpleNamespace(member_name="worker_idle_no_handle", status="ready"),
                SimpleNamespace(member_name="worker_shutdown", status="shut_down"),
            ]
        ),
    )
    agent._configurator.team_backend = fake_backend
    agent._spawn_manager.spawned_handles = {
        "worker_busy": object(),
        "worker_ready": object(),
    }
    agent._spawn_manager.cleanup_teammate = AsyncMock()
    agent._spawn_manager.restart_teammate = AsyncMock(return_value=True)

    new_session = create_agent_team_session(
        session_id=f"session_{uuid.uuid4().hex}",
        team_id="persistent_team",
    )

    await agent.resume_for_new_session(new_session)

    assert agent.session_id == new_session.get_session_id()
    fake_db.create_cur_session_tables.assert_awaited_once()
    assert agent._spawn_manager.cleanup_teammate.await_args_list == [
        call("worker_busy"),
        call("worker_ready"),
    ]
    assert agent._spawn_manager.restart_teammate.await_args_list == [
        call("worker_busy"),
        call("worker_ready"),
    ]
    assert fake_db.member.update_member_status.await_args_list == [
        call("worker_busy", "persistent_team", "error"),
        call("worker_busy", "persistent_team", "restarting"),
        call("worker_ready", "persistent_team", "error"),
        call("worker_ready", "persistent_team", "restarting"),
    ]


@pytest.mark.asyncio
async def test_team_agent_recover_for_existing_session_rebinds_live_teammates():
    leader_card = AgentCard(id=f"leader_{uuid.uuid4().hex}", name="leader", description="leader")
    agent = TeamAgent(card=leader_card)
    team_spec = TeamSpec(team_name="persistent_team", display_name="persistent_team")
    ctx = TeamRuntimeContext(role=TeamRole.LEADER, member_name="leader", team_spec=team_spec)
    agent._configurator._blueprint = TeamAgentBlueprint(
        card=leader_card,
        spec=TeamAgentSpec(agents={"leader": DeepAgentSpec()}, team_name="persistent_team"),
        ctx=ctx,
        language="en",
    )

    fake_db = AsyncMock()
    fake_backend = SimpleNamespace(
        team_name="persistent_team",
        db=fake_db,
        list_member_roster=AsyncMock(
            return_value=[
                SimpleNamespace(member_name="leader", status="ready"),
                SimpleNamespace(member_name="worker_busy", status="busy"),
                SimpleNamespace(member_name="worker_ready", status="ready"),
                SimpleNamespace(member_name="worker_idle_no_handle", status="ready"),
                SimpleNamespace(member_name="worker_shutdown", status="shut_down"),
            ]
        ),
    )
    agent._configurator.team_backend = fake_backend
    agent._spawn_manager.spawned_handles = {
        "worker_busy": object(),
        "worker_ready": object(),
    }
    agent._coordination.stop = AsyncMock()
    agent._spawn_manager.restart_teammate = AsyncMock(return_value=True)

    existing_session = create_agent_team_session(
        session_id=f"recover_{uuid.uuid4().hex}",
        team_id="persistent_team",
    )

    await agent.recover_for_existing_session(existing_session)

    assert agent.session_id == existing_session.get_session_id()
    agent._coordination.stop.assert_awaited_once()
    fake_db.create_cur_session_tables.assert_awaited_once()
    assert agent._spawn_manager.restart_teammate.await_args_list == [
        call("worker_busy"),
        call("worker_ready"),
    ]
    assert fake_db.member.update_member_status.await_args_list == [
        call("worker_busy", "persistent_team", "error"),
        call("worker_busy", "persistent_team", "restarting"),
        call("worker_ready", "persistent_team", "error"),
        call("worker_ready", "persistent_team", "restarting"),
    ]


def test_team_agent_recover_from_session_restores_session_id():
    from openjiuwen.agent_teams.runtime.metadata import write_team_namespace

    session_id = f"recover_state_{uuid.uuid4().hex}"
    session = create_agent_team_session(session_id=session_id, team_id="persistent_team")
    write_team_namespace(
        session,
        "persistent_team",
        {
            "spec": {
                "team_name": "persistent_team",
                "agents": {
                    "leader": {},
                },
            },
            "context": {
                "role": "leader",
                "member_name": "leader",
                "desc": "leader",
                "team_spec": {
                    "team_name": "persistent_team",
                    "display_name": "persistent_team",
                    "leader_member_name": "leader",
                },
                "messager_config": {},
                "db_config": {},
            },
        },
    )

    agent = TeamAgent.recover_from_session(session, "persistent_team")

    assert agent.session_id == session_id


def test_team_agent_recover_from_session_rides_current_model_config():
    """冷恢复的模型选择跟随当前 runtime spec，不吃 checkpoint 冻结的旧值：
    建团后换模型/模型改名下线时，恢复的团队应打新模型而不是旧 endpoint。
    逐成员按 role 键覆盖（live spec 没声明模型的成员保留旧值兜底），
    团队级 model_pool 整体换新。"""
    from openjiuwen.agent_teams.runtime.metadata import write_team_namespace
    from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
    from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec as _DeepAgentSpec
    from openjiuwen.agent_teams.schema.team import ModelPoolEntry
    from openjiuwen.core.foundation.llm import ModelClientConfig
    from openjiuwen.harness.schema.deep_agent_spec import TeamModelConfig

    session_id = f"recover_model_{uuid.uuid4().hex}"
    session = create_agent_team_session(session_id=session_id, team_id="persistent_team")
    write_team_namespace(
        session,
        "persistent_team",
        {
            "spec": {
                "team_name": "persistent_team",
                "agents": {
                    "leader": {
                        "model": {
                            "model_client_config": {
                                "model": "old-model",
                                "api_key": "k",
                                "api_base": "http://old",
                                "client_provider": "openai",
                            }
                        }
                    },
                    "teammate": {
                        "model": {
                            "model_client_config": {
                                "model": "old-member-model",
                                "api_key": "k",
                                "api_base": "http://old",
                                "client_provider": "openai",
                            }
                        }
                    },
                },
                "model_pool": [
                    {
                        "model_name": "old-pool-model",
                        "api_key": "k",
                        "api_base_url": "http://old",
                        "api_provider": "openai",
                    }
                ],
            },
            "context": {
                "role": "leader",
                "member_name": "leader",
                "desc": "leader",
                "team_spec": {
                    "team_name": "persistent_team",
                    "display_name": "persistent_team",
                    "leader_member_name": "leader",
                },
                "messager_config": {},
                "db_config": {},
            },
        },
    )

    def _model(name: str) -> TeamModelConfig:
        return TeamModelConfig(
            model_client_config=ModelClientConfig(
                model=name, api_key="k", api_base="http://new", client_provider="openai"
            )
        )

    fresh_spec = TeamAgentSpec(
        team_name="persistent_team",
        agents={
            # teammate 故意缺席：验证"live spec 未声明的成员保留旧模型"兜底
            "leader": _DeepAgentSpec(model=_model("new-model")),
        },
        model_pool=[
            ModelPoolEntry(
                model_name="new-pool-model",
                api_key="k",
                api_base_url="http://new",
                api_provider="openai",
            )
        ],
    )

    captured: dict = {}
    original_configure = TeamAgent.configure

    def spy_configure(self, spec, context, *args, **kwargs):
        captured["spec"] = spec
        return original_configure(self, spec, context, *args, **kwargs)

    with patch.object(TeamAgent, "configure", spy_configure):
        TeamAgent.recover_from_session(session, "persistent_team", runtime_spec=fresh_spec)

    recovered = captured["spec"]
    leader_model = recovered.agents["leader"].model.model_client_config
    assert leader_model.model == "new-model"
    assert leader_model.api_base == "http://new"
    # live spec 未声明 teammate 模型：保留持久化值兜底
    assert recovered.agents["teammate"].model.model_client_config.model == "old-member-model"
    # 团队级 pool 整体换成当前配置
    assert [entry.model_name for entry in recovered.model_pool] == ["new-pool-model"]


def test_team_agent_recover_from_session_drops_retired_element_types():
    """冷恢复重放的持久化 spec 可含已退役的 provider 类型（如平台版本删了
    swarm.user_todos）：恢复路径剥掉未注册条目降级为"缺席"，而不是让整个
    团队重建在 Unknown tool type 上抛出异常导致链路执行失败。新建路径保持 fail-fast 不受影响。"""
    from openjiuwen.agent_teams.runtime.metadata import write_team_namespace

    session_id = f"recover_retired_{uuid.uuid4().hex}"
    session = create_agent_team_session(session_id=session_id, team_id="persistent_team")
    write_team_namespace(
        session,
        "persistent_team",
        {
            "spec": {
                "team_name": "persistent_team",
                "agents": {
                    "leader": {
                        "tools": [{"type": "swarm.user_todos", "params": {}}],
                        "rails": [{"type": "swarm.retired_rail", "params": {}}],
                    },
                },
            },
            "context": {
                "role": "leader",
                "member_name": "leader",
                "desc": "leader",
                "team_spec": {
                    "team_name": "persistent_team",
                    "display_name": "persistent_team",
                    "leader_member_name": "leader",
                },
                "messager_config": {},
                "db_config": {},
            },
        },
    )

    agent = TeamAgent.recover_from_session(session, "persistent_team")

    assert agent.session_id == session_id


def test_team_agent_recover_from_session_builds_leader_member_handle():
    """A cold-recovered leader gets its TeamMember handle via configure().

    Recovery rebuilds the leader through ``configure()`` -> ``_setup_agent``;
    the team row already exists in the DB, so the handle must be populated
    and able to track status -- not left ``None`` as it was while the
    leader handle depended on a never-firing lazy-init callback.
    """
    from openjiuwen.agent_teams.runtime.metadata import write_team_namespace

    session_id = f"recover_handle_{uuid.uuid4().hex}"
    session = create_agent_team_session(session_id=session_id, team_id="persistent_team")
    write_team_namespace(
        session,
        "persistent_team",
        {
            "spec": {
                "team_name": "persistent_team",
                "agents": {
                    "leader": {},
                },
            },
            "context": {
                "role": "leader",
                "member_name": "leader",
                "desc": "leader",
                "team_spec": {
                    "team_name": "persistent_team",
                    "display_name": "persistent_team",
                    "leader_member_name": "leader",
                },
                "messager_config": {},
                "db_config": {},
            },
        },
    )

    agent = TeamAgent.recover_from_session(session, "persistent_team")

    handle = agent._state.team_member
    assert handle is not None
    assert handle.member_name == "leader"


@pytest.mark.asyncio
async def test_team_session_forwards_child_stream_output_with_source_tags(isolated_checkpointer):
    session_id = f"stream_{uuid.uuid4().hex}"
    team_session = create_agent_team_session(session_id=session_id, team_id="stream_team")
    await team_session.pre_run(inputs={"query": "hello"})

    child_session = team_session.create_agent_session(agent_id="worker_a")
    await child_session.pre_run(inputs={"payload": "child"})
    await child_session.write_stream({"kind": "agent"})
    await child_session.post_run()

    await team_session.write_stream({"kind": "team"})
    await team_session.post_run()

    chunks = []
    async for chunk in team_session.stream_iterator():
        chunks.append(chunk)

    assert any(
        getattr(chunk, "payload", {}).get("source_agent_id") == "worker_a"
        and getattr(chunk, "payload", {}).get("source_team_id") == "stream_team"
        for chunk in chunks
    )
    assert any(
        getattr(chunk, "payload", {}).get("kind") == "team"
        and getattr(chunk, "payload", {}).get("source_team_id") == "stream_team"
        for chunk in chunks
    )

    await isolated_checkpointer.release(session_id)


# ---------------------------------------------------------------------------
# TeamRuntimeManager release_session tests
# ---------------------------------------------------------------------------


class TestTeamRuntimeManagerReleaseSession:
    """Test TeamRuntimeManager.release_session."""

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_release_session_drops_tables_for_inactive_session(self, isolated_checkpointer):
        """release_session should drop dynamic tables for a non-active session."""
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
        from openjiuwen.agent_teams.runtime.metadata import write_team_namespace
        from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType

        manager = TeamRuntimeManager()
        session_id = f"release_inactive_{uuid.uuid4().hex}"

        team_session = create_agent_team_session(session_id=session_id)
        db_config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
        await team_session.pre_run()
        write_team_namespace(
            team_session,
            "release_team",
            {
                "spec": {"team_name": "release_team"},
                "context": {
                    "role": "leader",
                    "member_name": "leader",
                    "db_config": db_config.model_dump(),
                },
            },
        )
        await team_session.post_run()

        fake_db = AsyncMock()
        fake_db.initialize = AsyncMock()
        fake_db.drop_session_tables_by_id = AsyncMock(return_value=["team_task_xxx"])

        with patch("openjiuwen.agent_teams.spawn.shared_resources.get_shared_db", return_value=fake_db):
            await manager.release_session(session_id)

        fake_db.initialize.assert_awaited_once()
        fake_db.drop_session_tables_by_id.assert_awaited_once_with(session_id)

        # No team should be active for the released session.
        assert await manager.pool.teams_for_session(session_id) == []

        await isolated_checkpointer.release(session_id)

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_release_session_rejects_when_team_active(self):
        """release_session must refuse while a team is active on that session."""
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
        from openjiuwen.core.common.exception.errors import ValidationError

        manager = TeamRuntimeManager()
        session_id = f"release_active_{uuid.uuid4().hex}"
        team_name = "active_release_team"

        fake_agent = FakeTeamAgent(team_name, stream_label="team.chunk")
        await _activate_pool_entry(manager, team_name, session_id, fake_agent)

        with pytest.raises(ValidationError, match="busy"):
            await manager.release_session(session_id)

        # The runtime keeps holding the pool entry until stop_team is called.
        entry = await manager.pool.get(team_name)
        assert entry is not None
        assert entry.current_session_id == session_id
        assert entry.agent is fake_agent
        assert fake_agent.stop_calls == 0

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_release_session_force_stops_active_teams_before_cleanup(self):
        """release_session(force=True) tears down active teams before dropping tables."""
        from openjiuwen.agent_teams.runtime.manager import (
            TeamRuntimeManager,
            TeamSessionReleaseInfo,
        )
        from openjiuwen.agent_teams.tools.database import (
            DatabaseConfig,
            DatabaseType,
        )

        manager = TeamRuntimeManager()
        session_id = f"release_force_{uuid.uuid4().hex}"
        team_name = "force_release_team"

        fake_agent = FakeTeamAgent(team_name, stream_label="team.chunk")
        await _activate_pool_entry(manager, team_name, session_id, fake_agent)

        db_config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
        release_info = TeamSessionReleaseInfo(team_names=[team_name], db_config=db_config)
        fake_db = AsyncMock()
        fake_db.initialize = AsyncMock()
        fake_db.drop_session_tables_by_id = AsyncMock(return_value=[])

        with (
            patch(
                "openjiuwen.agent_teams.runtime.manager.TeamRuntimeManager.resolve_team_session_release_info",
                return_value=release_info,
            ),
            patch(
                "openjiuwen.agent_teams.spawn.shared_resources.get_shared_db",
                return_value=fake_db,
            ),
        ):
            await manager.release_session(session_id, force=True)

        assert fake_agent.stop_calls == 1
        assert await manager.pool.has_active(team_name) is False
        fake_db.drop_session_tables_by_id.assert_awaited_once_with(session_id)

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_release_session_empty_session_id_returns_early(self):
        """release_session should return early for empty session_id."""
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager

        manager = TeamRuntimeManager()
        # Should not raise or do anything
        await manager.release_session("")
        await manager.release_session(None)

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_release_session_raises_on_missing_context(self, isolated_checkpointer):
        """release_session should raise RuntimeError if all team buckets miss context."""
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
        from openjiuwen.agent_teams.runtime.metadata import write_team_namespace

        manager = TeamRuntimeManager()
        session_id = f"release_no_context_{uuid.uuid4().hex}"

        team_session = create_agent_team_session(session_id=session_id)
        await team_session.pre_run()
        write_team_namespace(team_session, "no_context_team", {"spec": {"team_name": "no_context_team"}})
        await team_session.post_run()

        with pytest.raises(RuntimeError, match="Cannot resolve team session release info"):
            await manager.release_session(session_id)

        await isolated_checkpointer.release(session_id)


class TestTeamRuntimeManagerInteract:
    """Test TeamRuntimeManager.interact payload dispatch and gate behaviour."""

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_interact_god_view_routes_to_deliver_input(self):
        """GodViewMessage must invoke ``agent.deliver_input`` and report success."""
        from openjiuwen.agent_teams.interaction.payload import GodViewMessage
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager

        manager = TeamRuntimeManager()
        team_name = "god_team"
        session_id = "s-god"

        delivered: list[str] = []

        class _Agent:
            team_backend = None

            async def deliver_input(self, content):
                delivered.append(content)

        await _activate_pool_entry(manager, team_name, session_id, _Agent())

        result = await manager.interact(
            GodViewMessage(body="hi"),
            team_name=team_name,
            session_id=session_id,
        )
        assert result.ok is True
        assert delivered == ["hi"]

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_interact_returns_not_active_when_no_pool_entry(self):
        from openjiuwen.agent_teams.interaction.payload import GodViewMessage
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager

        manager = TeamRuntimeManager()
        result = await manager.interact(
            GodViewMessage(body="x"),
            team_name="missing",
            session_id="missing",
        )
        assert result.ok is False
        assert result.reason == "not_active"

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_interact_returns_gate_closed_after_close_and_drain(self):
        from openjiuwen.agent_teams.interaction.payload import GodViewMessage
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager

        manager = TeamRuntimeManager()
        team_name = "drained"
        session_id = "s1"

        class _Agent:
            team_backend = None

            async def deliver_input(self, content):
                pass

        await _activate_pool_entry(manager, team_name, session_id, _Agent())
        entry = await manager.pool.get(team_name)
        assert entry is not None
        await entry.interact_gate.close_and_drain()

        result = await manager.interact(
            GodViewMessage(body="x"),
            team_name=team_name,
            session_id=session_id,
        )
        assert result.ok is False
        assert result.reason == "gate_closed"

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_interact_string_input_via_runner_treated_as_god_view(self):
        """Runner.interact_agent_team accepts a bare str as god-view sugar."""
        await Runner.start()
        try:
            session_id = f"runner_god_{uuid.uuid4().hex}"
            spec = _runtime_spec("god_runner_team")
            agent = FakeTeamAgent("god_runner_team", stream_label="team.chunk")

            with patch.object(TeamAgentSpec, "build", return_value=agent):
                async for _ in Runner.run_agent_team_streaming(
                        agent_team=spec,
                        inputs={"query": "first"},
                        session=session_id,
                ):
                    pass

                # Stream ended -> gate closed; god-view interact should be
                # rejected via DeliverResult, not raise.
                result = await Runner.interact_agent_team(
                    "follow",
                    team_name="god_runner_team",
                    session_id=session_id,
                )
                assert result.ok is False
                assert result.reason == "gate_closed"
        finally:
            await Runner.stop()

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_interact_runner_binds_target_team_session_context(self):
        """Runner.interact_agent_team should bind the target session at the entrypoint."""
        from openjiuwen.agent_teams.context import get_session_id
        from openjiuwen.core.runner.team_runner import _global_runner

        await Runner.start()
        try:
            manager = _global_runner()._get_team_runtime_manager()
            team_name = "runner_context_team"
            session_id = f"runner_context_{uuid.uuid4().hex}"

            delivered: list[str] = []
            seen_context: list[str] = []

            class _Agent:
                team_backend = None

                async def deliver_input(self, content):
                    delivered.append(content)
                    seen_context.append(get_session_id())

            await _activate_pool_entry(manager, team_name, session_id, _Agent())

            result = await Runner.interact_agent_team(
                "follow",
                team_name=team_name,
                session_id=session_id,
            )

            assert result.ok is True
            assert delivered == ["follow"]
            assert seen_context == [session_id]
        finally:
            await Runner.stop()


class TestTeamRuntimeManagerStopTeam:
    """Test TeamRuntimeManager.stop_team."""

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_stop_team_returns_true_and_clears_pool_entry(self):
        """stop_team must clear active pointers and remove the pool entry."""
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager

        manager = TeamRuntimeManager()
        team_name = "stop_team"
        session_id = f"stop_{uuid.uuid4().hex}"

        fake_agent = FakeTeamAgent(team_name, stream_label="team.chunk")
        await _activate_pool_entry(manager, team_name, session_id, fake_agent)
        assert await manager.pool.has_active(team_name) is True

        result = await manager.stop_team(team_name=team_name, session_id=session_id)
        assert result is True
        assert fake_agent.stop_calls == 1
        assert await manager.pool.has_active(team_name) is False
        assert await manager.pool.list_team_names() == []

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_stop_team_returns_false_when_team_mismatch(self):
        """stop_team must refuse a mismatched (team, session) pair."""
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager

        manager = TeamRuntimeManager()
        team_name = "active_team"
        session_id = "s1"

        fake_agent = FakeTeamAgent(team_name, stream_label="team.chunk")
        await _activate_pool_entry(manager, team_name, session_id, fake_agent)

        result = await manager.stop_team(team_name="other_team", session_id=session_id)
        assert result is False
        assert fake_agent.stop_calls == 0
        assert await manager.pool.has_active(team_name) is True

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_get_monitor_returns_monitor_for_matching_entry(self):
        """get_monitor should create a monitor only for the exact team/session pair."""
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager

        manager = TeamRuntimeManager()
        team_name = "monitor_team"
        session_id = "s1"
        fake_agent = FakeTeamAgent(team_name, stream_label="team.chunk")
        await _activate_pool_entry(manager, team_name, session_id, fake_agent)

        observed_session_ids = []

        def _create_monitor(_agent, *, hide_dm=False):
            observed_session_ids.append(get_session_id())
            return "dm_hidden" if hide_dm else "monitor"

        caller_token = set_session_id("caller-session")
        try:
            with patch(
                    "openjiuwen.agent_teams.runtime.manager.create_monitor",
                    side_effect=_create_monitor,
            ) as mocked:
                assert await manager.get_monitor(team_name=team_name, session_id=session_id) == "monitor"
                mocked.assert_called_once_with(fake_agent, hide_dm=False)
                assert get_session_id() == "caller-session"

            with patch(
                    "openjiuwen.agent_teams.runtime.manager.create_monitor",
                    side_effect=_create_monitor,
            ) as mocked:
                assert (
                        await manager.get_monitor(team_name=team_name, session_id=session_id,
                                                  hide_dm=True) == "dm_hidden"
                )
                mocked.assert_called_once_with(fake_agent, hide_dm=True)
                assert get_session_id() == "caller-session"
        finally:
            reset_session_id(caller_token)

        assert await manager.get_monitor(team_name=team_name, session_id="other") is None
        assert await manager.get_monitor(team_name="missing", session_id=session_id) is None
        assert observed_session_ids == [session_id, session_id]

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_stop_team_then_delete_team_succeeds(self):
        """After stop_team, delete_team should be allowed (busy guard cleared)."""
        from openjiuwen.agent_teams.runtime.manager import (
            TeamRuntimeManager,
            TeamSessionReleaseInfo,
        )
        from openjiuwen.agent_teams.tools.database import (
            DatabaseConfig,
            DatabaseType,
        )

        manager = TeamRuntimeManager()
        team_name = "stop_then_delete"
        session_id = f"std_{uuid.uuid4().hex}"

        fake_agent = FakeTeamAgent(team_name, stream_label="team.chunk")
        await _activate_pool_entry(manager, team_name, session_id, fake_agent)
        await manager.stop_team(team_name=team_name, session_id=session_id)

        db_config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
        release_info = TeamSessionReleaseInfo(team_names=[team_name], db_config=db_config)
        fake_db = AsyncMock()
        fake_db.initialize = AsyncMock()
        fake_db.drop_session_tables_by_id = AsyncMock(return_value=[])
        fake_db.team = AsyncMock()
        fake_db.team.delete_team = AsyncMock(return_value=True)
        fake_checkpointer = AsyncMock()
        fake_checkpointer.session_exists = AsyncMock(return_value=True)
        fake_checkpointer.release = AsyncMock()

        with (
            patch(
                "openjiuwen.agent_teams.runtime.manager.TeamRuntimeManager.resolve_team_session_release_info",
                return_value=release_info,
            ),
            patch(
                "openjiuwen.agent_teams.spawn.shared_resources.get_shared_db",
                return_value=fake_db,
            ),
            patch(
                "openjiuwen.agent_teams.runtime.manager.CheckpointerFactory.get_checkpointer",
                return_value=fake_checkpointer,
            ),
        ):
            result = await manager.delete_team(team_name, [session_id])

        assert result is True
        fake_db.team.delete_team.assert_awaited_once_with(team_name)


class TestTeamRuntimeManagerDeleteTeam:
    """Test TeamRuntimeManager.delete_team db_config resolution."""

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_delete_team_fetches_db_config_from_session(self, isolated_checkpointer):
        """delete_team should fetch db_config from session state."""
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
        from openjiuwen.agent_teams.runtime.metadata import write_team_namespace
        from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType

        manager = TeamRuntimeManager()
        session_id = f"delete_team_config_{uuid.uuid4().hex}"
        team_name = "delete_config_team"

        db_config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string="delete.db")
        team_session = create_agent_team_session(session_id=session_id)
        await team_session.pre_run()
        write_team_namespace(
            team_session,
            team_name,
            {
                "spec": {"team_name": team_name},
                "context": {
                    "role": "leader",
                    "member_name": "leader",
                    "db_config": db_config.model_dump(),
                },
            },
        )
        await team_session.post_run()

        fake_db = AsyncMock()
        fake_db.initialize = AsyncMock()
        fake_db.drop_session_tables_by_id = AsyncMock(return_value=["team_task_xxx"])
        fake_db.team = AsyncMock()
        fake_db.team.delete_team = AsyncMock(return_value=True)

        with patch("openjiuwen.agent_teams.spawn.shared_resources.get_shared_db", return_value=fake_db):
            result = await manager.delete_team(team_name, [session_id])

        assert result is True
        fake_db.initialize.assert_awaited_once()
        fake_db.drop_session_tables_by_id.assert_awaited_once_with(session_id)
        fake_db.team.delete_team.assert_awaited_once_with(team_name)

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_delete_team_uses_first_parseable_release_info(self):
        """delete_team should skip bad sessions and use the first parseable release info."""
        from openjiuwen.agent_teams.runtime.manager import (
            TeamRuntimeManager,
            TeamSessionReleaseInfo,
        )
        from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType

        manager = TeamRuntimeManager()
        team_name = "delete_parseable_team"
        bad_session_id = f"bad_{uuid.uuid4().hex}"
        good_session_id = f"good_{uuid.uuid4().hex}"

        db_config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
        release_info = TeamSessionReleaseInfo(team_names=[team_name], db_config=db_config)
        fake_db = AsyncMock()
        fake_db.initialize = AsyncMock()
        fake_db.drop_session_tables_by_id = AsyncMock(return_value=[])
        fake_db.team = AsyncMock()
        fake_db.team.delete_team = AsyncMock(return_value=True)
        fake_checkpointer = AsyncMock()
        fake_checkpointer.session_exists = AsyncMock(return_value=True)
        fake_checkpointer.release = AsyncMock()

        async def _fake_resolve(session_id: str):
            if session_id == bad_session_id:
                raise RuntimeError(f"Cannot resolve team session release info for {session_id}")
            if session_id == good_session_id:
                return release_info
            return None

        with (
            patch(
                "openjiuwen.agent_teams.runtime.manager.TeamRuntimeManager.resolve_team_session_release_info",
                side_effect=_fake_resolve,
            ),
            patch(
                "openjiuwen.agent_teams.spawn.shared_resources.get_shared_db",
                return_value=fake_db,
            ),
            patch(
                "openjiuwen.agent_teams.runtime.manager.CheckpointerFactory.get_checkpointer",
                return_value=fake_checkpointer,
            ),
        ):
            result = await manager.delete_team(team_name, [bad_session_id, good_session_id])

        assert result is True
        fake_db.drop_session_tables_by_id.assert_any_await(bad_session_id)
        fake_db.drop_session_tables_by_id.assert_any_await(good_session_id)
        assert fake_db.drop_session_tables_by_id.await_count == 2
        fake_checkpointer.release.assert_any_await(bad_session_id)
        fake_checkpointer.release.assert_any_await(good_session_id)
        assert fake_checkpointer.release.await_count == 2
        fake_db.team.delete_team.assert_awaited_once_with(team_name)

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_delete_team_skips_sessions_with_no_team_bucket(self):
        """delete_team should skip sessions that resolve to None and pick the next one.

        Production-realistic case: the first session has no persisted team
        bucket (resolver returns ``None``, not raise). Helper must continue
        scanning rather than treat ``None`` as the final answer.
        """
        from openjiuwen.agent_teams.runtime.manager import (
            TeamRuntimeManager,
            TeamSessionReleaseInfo,
        )
        from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType

        manager = TeamRuntimeManager()
        team_name = "delete_none_skip_team"
        empty_session_id = f"empty_{uuid.uuid4().hex}"
        good_session_id = f"good_{uuid.uuid4().hex}"

        db_config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
        release_info = TeamSessionReleaseInfo(team_names=[team_name], db_config=db_config)
        fake_db = AsyncMock()
        fake_db.initialize = AsyncMock()
        fake_db.drop_session_tables_by_id = AsyncMock(return_value=[])
        fake_db.team = AsyncMock()
        fake_db.team.delete_team = AsyncMock(return_value=True)
        fake_checkpointer = AsyncMock()
        fake_checkpointer.session_exists = AsyncMock(return_value=True)
        fake_checkpointer.release = AsyncMock()

        async def _fake_resolve(session_id: str):
            if session_id == empty_session_id:
                return None
            if session_id == good_session_id:
                return release_info
            return None

        with (
            patch(
                "openjiuwen.agent_teams.runtime.manager.TeamRuntimeManager.resolve_team_session_release_info",
                side_effect=_fake_resolve,
            ),
            patch(
                "openjiuwen.agent_teams.spawn.shared_resources.get_shared_db",
                return_value=fake_db,
            ),
            patch(
                "openjiuwen.agent_teams.runtime.manager.CheckpointerFactory.get_checkpointer",
                return_value=fake_checkpointer,
            ),
        ):
            result = await manager.delete_team(team_name, [empty_session_id, good_session_id])

        assert result is True
        fake_db.team.delete_team.assert_awaited_once_with(team_name)

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_delete_team_raises_when_existing_sessions_do_not_resolve(self):
        """delete_team must raise when every existing session is unusable.

        Pins the helper-returns-Optional / caller-raises contract so the
        two never drift back into the old dual-raise shape.
        """
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager

        manager = TeamRuntimeManager()
        team_name = "delete_all_unusable_team"
        bad_session_id = f"bad_{uuid.uuid4().hex}"
        empty_session_id = f"empty_{uuid.uuid4().hex}"

        async def _fake_resolve(session_id: str):
            if session_id == bad_session_id:
                raise RuntimeError(f"Cannot resolve team session release info for {session_id}")
            return None

        fake_checkpointer = AsyncMock()
        fake_checkpointer.session_exists = AsyncMock(return_value=True)

        with (
            patch(
                "openjiuwen.agent_teams.runtime.manager.TeamRuntimeManager.resolve_team_session_release_info",
                side_effect=_fake_resolve,
            ),
            patch(
                "openjiuwen.agent_teams.runtime.manager.CheckpointerFactory.get_checkpointer",
                return_value=fake_checkpointer,
            ),
        ):
            with pytest.raises(RuntimeError, match="any supplied sessions"):
                await manager.delete_team(team_name, [bad_session_id, empty_session_id])

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_delete_team_succeeds_when_supplied_sessions_are_already_released(self):
        """delete_team should be idempotent after supplied sessions are gone."""
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager

        manager = TeamRuntimeManager()
        team_name = "delete_already_released_team"
        session_id = f"released_{uuid.uuid4().hex}"
        fake_checkpointer = AsyncMock()
        fake_checkpointer.session_exists = AsyncMock(return_value=False)
        fake_checkpointer.release = AsyncMock()

        with (
            patch(
                "openjiuwen.agent_teams.runtime.manager.CheckpointerFactory.get_checkpointer",
                return_value=fake_checkpointer,
            ),
            patch(
                "openjiuwen.agent_teams.runtime.manager.TeamRuntimeManager.resolve_team_session_release_info",
                new_callable=AsyncMock,
            ) as resolve_mock,
            patch("openjiuwen.agent_teams.spawn.shared_resources.get_shared_db") as get_shared_db_mock,
        ):
            result = await manager.delete_team(team_name, [session_id])

        assert result is True
        fake_checkpointer.session_exists.assert_awaited_once_with(session_id)
        fake_checkpointer.release.assert_not_awaited()
        resolve_mock.assert_not_awaited()
        get_shared_db_mock.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_delete_team_rejects_when_team_active(self):
        """delete_team must refuse while the target team is active in the pool."""
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
        from openjiuwen.core.common.exception.errors import ValidationError

        manager = TeamRuntimeManager()
        team_name = "delete_active_team"
        session_id = f"delete_active_{uuid.uuid4().hex}"

        fake_agent = FakeTeamAgent(team_name, stream_label="team.chunk")
        await _activate_pool_entry(manager, team_name, session_id, fake_agent)

        with pytest.raises(ValidationError, match="busy"):
            await manager.delete_team(team_name, [session_id])

        # Pool entry must remain after the rejection so the caller can stop it.
        entry = await manager.pool.get(team_name)
        assert entry is not None
        assert entry.agent is fake_agent

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_delete_team_force_stops_active_runtime_before_cleanup(self):
        """delete_team(force=True) stops the active runtime in-line."""
        from openjiuwen.agent_teams.runtime.manager import (
            TeamRuntimeManager,
            TeamSessionReleaseInfo,
        )
        from openjiuwen.agent_teams.tools.database import (
            DatabaseConfig,
            DatabaseType,
        )

        manager = TeamRuntimeManager()
        team_name = "force_delete_team"
        session_id = f"force_del_{uuid.uuid4().hex}"

        fake_agent = FakeTeamAgent(team_name, stream_label="team.chunk")
        await _activate_pool_entry(manager, team_name, session_id, fake_agent)

        db_config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
        release_info = TeamSessionReleaseInfo(team_names=[team_name], db_config=db_config)
        fake_db = AsyncMock()
        fake_db.initialize = AsyncMock()
        fake_db.drop_session_tables_by_id = AsyncMock(return_value=[])
        fake_db.team = AsyncMock()
        fake_db.team.delete_team = AsyncMock(return_value=True)
        fake_checkpointer = AsyncMock()
        fake_checkpointer.session_exists = AsyncMock(return_value=True)
        fake_checkpointer.release = AsyncMock()

        with (
            patch(
                "openjiuwen.agent_teams.runtime.manager.TeamRuntimeManager.resolve_team_session_release_info",
                return_value=release_info,
            ),
            patch(
                "openjiuwen.agent_teams.spawn.shared_resources.get_shared_db",
                return_value=fake_db,
            ),
            patch(
                "openjiuwen.agent_teams.runtime.manager.CheckpointerFactory.get_checkpointer",
                return_value=fake_checkpointer,
            ),
        ):
            ok = await manager.delete_team(team_name, [session_id], force=True)

        assert ok is True
        assert fake_agent.stop_calls == 1
        assert await manager.pool.has_active(team_name) is False
        fake_db.team.delete_team.assert_awaited_once_with(team_name)


class TestRunnerReleaseAutoDispatch:
    """Test Runner.release() auto-dispatch between team and non-team sessions."""

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_runner_release_non_team_session_simple_checkpoint_release(self, isolated_checkpointer):
        """Runner.release should only release checkpoint for non-team session."""
        await Runner.start()
        session_id = f"non_team_{uuid.uuid4().hex}"

        await Runner.release(session_id)

        # Checkpoint should NOT exist (simple release was called)
        assert await isolated_checkpointer.session_exists(session_id) is False

        await Runner.stop()

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_runner_release_team_session_cleans_dynamic_tables(self, isolated_checkpointer):
        """Runner.release should clean dynamic tables for team session."""
        from openjiuwen.agent_teams.runtime.metadata import write_team_namespace
        from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType

        await Runner.start()
        session_id = f"team_auto_{uuid.uuid4().hex}"

        db_config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
        team_session = create_agent_team_session(session_id=session_id)
        await team_session.pre_run()
        write_team_namespace(
            team_session,
            "auto_team",
            {
                "spec": {"team_name": "auto_team"},
                "context": {
                    "role": "leader",
                    "member_name": "leader",
                    "db_config": db_config.model_dump(),
                },
            },
        )
        await team_session.post_run()

        fake_db = AsyncMock()
        fake_db.initialize = AsyncMock()
        fake_db.drop_session_tables_by_id = AsyncMock(return_value=["team_task_xxx"])

        with (
            patch("openjiuwen.agent_teams.spawn.shared_resources.get_shared_db", return_value=fake_db),
            patch(
                "openjiuwen.agent_teams.runtime.manager.remove_session_worktrees",
                new_callable=AsyncMock,
            ) as remove_worktrees,
        ):
            await Runner.release(session_id)

        # Should have called drop_session_tables_by_id (team cleanup)
        fake_db.initialize.assert_awaited_once()
        fake_db.drop_session_tables_by_id.assert_awaited_once_with(session_id)
        remove_worktrees.assert_awaited_once_with("auto_team", session_id)

        # Checkpoint should be released
        assert await isolated_checkpointer.session_exists(session_id) is False

        await Runner.stop()

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_runner_release_multi_team_session_cleans_dynamic_tables(self, isolated_checkpointer):
        """Runner.release should clean session tables when one session contains multiple teams."""
        from openjiuwen.agent_teams.runtime.metadata import write_team_namespace
        from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType

        await Runner.start()
        session_id = f"team_multi_{uuid.uuid4().hex}"
        db_config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")

        team_session = create_agent_team_session(session_id=session_id)
        await team_session.pre_run()
        for team_name in ("first_team", "second_team"):
            write_team_namespace(
                team_session,
                team_name,
                {
                    "spec": {"team_name": team_name},
                    "context": {
                        "role": "leader",
                        "member_name": "leader",
                        "db_config": db_config.model_dump(),
                    },
                },
            )
        await team_session.post_run()

        fake_db = AsyncMock()
        fake_db.initialize = AsyncMock()
        fake_db.drop_session_tables_by_id = AsyncMock(return_value=["team_task_xxx"])

        with (
            patch("openjiuwen.agent_teams.spawn.shared_resources.get_shared_db", return_value=fake_db),
            patch(
                "openjiuwen.agent_teams.runtime.manager.remove_session_worktrees",
                new_callable=AsyncMock,
            ) as remove_worktrees,
        ):
            await Runner.release(session_id)

        fake_db.drop_session_tables_by_id.assert_awaited_once_with(session_id)
        remove_worktrees.assert_has_awaits(
            [
                call("first_team", session_id),
                call("second_team", session_id),
            ]
        )
        assert await isolated_checkpointer.session_exists(session_id) is False

        await Runner.stop()

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_runner_release_team_session_uses_default_db_config(self, isolated_checkpointer):
        """Runner.release should use TeamRuntimeContext defaults when db_config is omitted."""
        from openjiuwen.agent_teams.runtime.metadata import write_team_namespace

        await Runner.start()
        session_id = f"team_missing_config_{uuid.uuid4().hex}"

        team_session = create_agent_team_session(session_id=session_id)
        await team_session.pre_run()
        write_team_namespace(
            team_session,
            "missing_config_team",
            {
                "spec": {"team_name": "missing_config_team"},
                "context": {
                    "role": "leader",
                    "member_name": "leader",
                },
            },
        )
        await team_session.post_run()

        fake_db = AsyncMock()
        fake_db.initialize = AsyncMock()
        fake_db.drop_session_tables_by_id = AsyncMock(return_value=["team_task_xxx"])

        with patch("openjiuwen.agent_teams.spawn.shared_resources.get_shared_db", return_value=fake_db):
            await Runner.release(session_id)

        fake_db.drop_session_tables_by_id.assert_awaited_once_with(session_id)
        assert await isolated_checkpointer.session_exists(session_id) is False

        await Runner.stop()

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_runner_release_team_session_invalid_context_uses_defaults(self, isolated_checkpointer):
        """Runner.release should tolerate partially populated TeamRuntimeContext state."""
        from openjiuwen.agent_teams.runtime.metadata import write_team_namespace

        await Runner.start()
        session_id = f"team_bad_context_{uuid.uuid4().hex}"

        team_session = create_agent_team_session(session_id=session_id)
        await team_session.pre_run()
        write_team_namespace(
            team_session,
            "bad_context_team",
            {
                "spec": {"team_name": "bad_context_team"},
                "context": {"invalid": "data"},
            },
        )
        await team_session.post_run()

        fake_db = AsyncMock()
        fake_db.initialize = AsyncMock()
        fake_db.drop_session_tables_by_id = AsyncMock(return_value=["team_task_xxx"])

        with patch("openjiuwen.agent_teams.spawn.shared_resources.get_shared_db", return_value=fake_db):
            await Runner.release(session_id)

        fake_db.drop_session_tables_by_id.assert_awaited_once_with(session_id)
        assert await isolated_checkpointer.session_exists(session_id) is False

        await Runner.stop()


# ---------------------------------------------------------------------------
# Single facade with ``base`` flag: default agent_teams path,
# ``base=True`` switches to multi_agent BaseTeam.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.level0
async def test_run_agent_team_rejects_unactivated_team_name():
    """``str`` input must fail when no pool entry and no session bucket exist.

    The recover path through ``name + old_session`` reads the spec from
    the session bucket when the pool is empty (e.g. after a stop or a
    process restart). With ``session=None`` neither source is available,
    so the request must be rejected with a clear "first-time runs need
    a TeamAgentSpec" message.
    """
    from openjiuwen.core.common.exception.errors import ValidationError

    await Runner.start()
    with pytest.raises(ValidationError, match="has no live pool entry"):
        await Runner.run_agent_team(agent_team="never-seeded-team", inputs={"query": "x"})


@pytest.mark.asyncio
@pytest.mark.level0
async def test_run_agent_team_resolves_team_name_via_pool(isolated_checkpointer, stateful_team_db):
    """After spec activation seeds the pool, follow-up calls may pass team_name as str."""
    await Runner.start()
    team_name = f"reuse_{uuid.uuid4().hex}"
    spec = _runtime_spec(team_name)
    agent = FakeTeamAgent(team_name, stream_label="team.chunk", spec=spec)

    with patch.object(TeamAgentSpec, "build", return_value=agent):
        first_session = f"sess1_{uuid.uuid4().hex}"
        first = await Runner.run_agent_team(
            agent_team=spec,
            inputs={"query": "first"},
            session=first_session,
        )
        assert first["team_name"] == team_name
        # FakeTeamAgent bypasses BuildTeamTool, so flip the DB-side
        # team_exists signal manually before round 2 (see fixture docstring).
        await stateful_team_db.team.create_team(team_name)
        # Pool now holds an entry whose ``agent.spec`` is reachable —
        # the str-shorthand should resolve through it without a rebuild.
        second_session = f"sess2_{uuid.uuid4().hex}"
        second = await Runner.run_agent_team(
            agent_team=team_name,
            inputs={"query": "second"},
            session=second_session,
        )
        assert second["team_name"] == team_name
        assert agent.invoke_calls == 2


@pytest.mark.asyncio
@pytest.mark.level0
async def test_run_agent_team_rejects_team_agent_instance():
    """``TeamAgent`` instances are no longer accepted on the public entry."""
    from openjiuwen.core.common.exception.errors import ValidationError

    await Runner.start()
    leader_card = AgentCard(id="leader_reject", name="leader", description="leader")
    agent = TeamAgent(card=leader_card)
    with pytest.raises(ValidationError, match="run_agent_team accepts"):
        await Runner.run_agent_team(agent_team=agent, inputs={"query": "x"})


@pytest.mark.asyncio
@pytest.mark.level0
async def test_run_agent_team_base_true_rejects_team_agent_spec():
    """``TeamAgentSpec`` belongs to the default path, not ``base=True``."""
    from openjiuwen.core.common.exception.errors import ValidationError

    await Runner.start()
    spec = _runtime_spec("mis_routed")
    with pytest.raises(ValidationError, match=r"run_agent_team\(base=True\) accepts"):
        await Runner.run_agent_team(agent_team=spec, inputs={"query": "x"}, base=True)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_run_agent_team_base_true_accepts_base_team_instance(isolated_checkpointer):
    """``base=True`` + ``BaseTeam`` instance: invoke + post_run lifecycle wires correctly."""
    from openjiuwen.core.multi_agent import BaseTeam, TeamCard, TeamConfig

    class _StubTeam(BaseTeam):
        def __init__(self) -> None:
            super().__init__(
                card=TeamCard(id="stub_base", name="stub_base", description="stub"),
                config=TeamConfig(max_agents=1),
            )
            self.invoke_calls = 0

        async def invoke(self, message: Any, session=None) -> Any:
            self.invoke_calls += 1
            return {"echo": message, "session_id": session.get_session_id()}

        async def stream(self, message: Any, session=None) -> AsyncIterator[Any]:
            yield await self.invoke(message, session=session)

    await Runner.start()
    team = _StubTeam()
    session_id = f"base_team_{uuid.uuid4().hex}"
    result = await Runner.run_agent_team(
        agent_team=team,
        inputs={"payload": "hello"},
        base=True,
        session=session_id,
    )
    assert team.invoke_calls == 1
    assert result["echo"] == {"payload": "hello"}
    assert result["session_id"] == session_id


@pytest.mark.asyncio
@pytest.mark.level1
async def test_run_agent_team_base_true_resolves_team_id_via_resource_mgr(isolated_checkpointer):
    """``base=True`` + ``str`` resolves through ``Runner.resource_mgr``."""
    from openjiuwen.core.multi_agent import BaseTeam, TeamCard, TeamConfig

    class _StubTeam(BaseTeam):
        def __init__(self) -> None:
            super().__init__(
                card=TeamCard(id="stub_resolve", name="stub_resolve", description="stub"),
                config=TeamConfig(max_agents=1),
            )

        async def invoke(self, message: Any, session=None) -> Any:
            return {"resolved": True, "session_id": session.get_session_id()}

        async def stream(self, message: Any, session=None) -> AsyncIterator[Any]:
            yield await self.invoke(message, session=session)

    await Runner.start()
    team = _StubTeam()
    await Runner.resource_mgr.add_agent_team(team.card, lambda: team)
    try:
        result = await Runner.run_agent_team(
            agent_team=team.card.id,
            inputs={"x": 1},
            base=True,
        )
        assert result["resolved"] is True
    finally:
        await Runner.resource_mgr.remove_agent_team(team_id=team.card.id)
