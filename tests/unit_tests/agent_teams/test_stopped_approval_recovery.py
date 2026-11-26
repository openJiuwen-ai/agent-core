# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Recovery of approval mail addressed to a stopped native team member."""

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from openjiuwen.agent_teams.agent import agent_configurator
from openjiuwen.agent_teams.agent.coordination.event_bus import InnerEventMessage, InnerEventType
from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.harness.state import HarnessState
from openjiuwen.agent_teams.harness.team_harness import TeamHarness
from openjiuwen.agent_teams.messager.base import MessagerTransportConfig
from openjiuwen.agent_teams.rails.team_permission_rail import TeamPermissionRail
from openjiuwen.agent_teams.schema.blueprint import DeepAgentSpec, LeaderSpec, TeamAgentSpec
from openjiuwen.agent_teams.schema.status import MemberStatus, TaskStatus
from openjiuwen.agent_teams.schema.team import TeamRole, TeamRuntimeContext, TeamSpec
from openjiuwen.agent_teams.spawn import shared_resources
from openjiuwen.agent_teams.tools.database import DatabaseConfig
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent_team import create_agent_team_session
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.core.session.checkpointer.inmemory import InMemoryCheckpointer
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.factory import DeepAgentParts
from openjiuwen.harness.schema.config import DeepAgentConfig
from tests.unit_tests.agent_teams.test_pending_approval_native import CountEdit
from tests.unit_tests.fixtures.mock_llm import MockLLMModel, create_text_response


async def eventually(probe):
    async with asyncio.timeout(5):
        while not await probe():
            await asyncio.sleep(0.01)


@asynccontextmanager
async def stopped_worker(
    tmp_path, monkeypatch, *, pending=False, memory_db=False, checkpointer_type=InMemoryCheckpointer,
):
    # The process-wide SQLite pool belongs to the event loop that uses it.
    monkeypatch.setattr(shared_resources, "_db_instances", {})
    original_checkpointer = CheckpointerFactory.get_checkpointer()
    checkpointer = checkpointer_type()
    CheckpointerFactory.set_default_checkpointer(checkpointer)
    name = "approval-recovery-" + uuid4().hex
    session = create_agent_team_session(session_id=uuid4().hex)
    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec(), "teammate": DeepAgentSpec()},
        team_name=name,
        lifecycle="persistent",
        spawn_mode="inprocess",
        evolution_enabled=False,
        leader=LeaderSpec(member_name="leader", display_name="Leader", desc="Leader"),
    )
    database = DatabaseConfig(
        connection_string=":memory:" if memory_db else str(tmp_path / "team.db"),
    )
    ctx = TeamRuntimeContext(
        role=TeamRole.LEADER,
        member_name="leader",
        team_spec=TeamSpec(team_name=name, display_name=name, leader_member_name="leader"),
        db_config=database,
        messager_config=MessagerTransportConfig(team_name=name, node_id="leader"),
    )

    llm = MockLLMModel()
    llm.set_responses([
        AssistantMessage(content="", tool_calls=[ToolCall(
            id="call-a", type="function", name="edit_file", arguments="{}"
        )]),
        create_text_response("done"),
    ])
    monkeypatch.setattr(Model, "stream", lambda self, *args, **kwargs: llm.stream(*args, **kwargs))
    monkeypatch.setattr(Model, "invoke", lambda self, *args, **kwargs: llm.invoke(*args, **kwargs))

    tool = CountEdit()
    resolve = DeepAgentSpec.resolve_parts

    def resolve_worker(self, context=None):
        if self.card.id != f"{name}_worker":
            return resolve(self, context)
        model = Model(
            model_client_config=ModelClientConfig(
                client_provider="OpenAI", api_key="fake", api_base="http://unused.invalid"
            ),
            model_config=ModelRequestConfig(model="mock-model"),
        )
        return DeepAgentParts(
            config=DeepAgentConfig(
                card=self.card,
                model=model,
                cwd=str(tmp_path),
                enable_task_loop=True,
                enable_read_image_multimodal=False,
            ),
            rails=[TeamPermissionRail(
                config={"enabled": True, "defaults": {"*": "allow"}, "tools": {"edit_file": {"*": "ask"}}}
            )],
            tool_cards=[tool.card],
            tool_instances=[tool],
        )

    monkeypatch.setattr(DeepAgentSpec, "resolve_parts", resolve_worker)
    await Runner.start()
    leader = TeamAgent(AgentCard(id=f"{name}_leader", name="leader"))
    leader.configure(spec, ctx)
    leader.stream_controller.stream_queue = asyncio.Queue()
    backend = leader.team_backend
    manager = leader.spawn_manager
    try:
        await session.pre_run()
        await leader._coordination.start(session)
        await backend.build_team(name, "Test team", "Leader", "Leader")
        await leader.update_status(MemberStatus.READY)
        assert await backend.spawn_member("worker", "Worker", AgentCard(name="worker"))
        worker_context = await manager.build_context_from_db("worker")
        handle = await manager.spawn_teammate(worker_context, initial_message="edit" if pending else None)

        async def worker_settled():
            worker = handle.agent_ref
            member = await backend.get_member("worker")
            native = worker.resources.harness.get_deep_agent()
            live_and_ready = worker._coordination.is_running and member.status == MemberStatus.READY.value
            round_settled = native.state is HarnessState.IDLE and native.active_round is None
            return live_and_ready and round_settled and worker.has_pending_interrupt() == pending

        await eventually(worker_settled)
        await manager.cleanup_teammate("worker")
        assert await backend.db.member.update_member_status("worker", name, MemberStatus.STOPPED.value)
        assert not handle.is_alive
        assert await backend.db.task.create_task("terminal-task", name, "Done", "", TaskStatus.COMPLETED.value)
        yield leader, backend, manager, checkpointer, handle, tool
    finally:
        await leader.stop_coordination()
        await session.post_run()
        await Runner.stop()
        await backend.db.close()
        CheckpointerFactory.set_default_checkpointer(original_checkpointer)


async def poll_completion(leader):
    handler = leader._coordination.dispatcher.team_completion
    await handler.on_poll_task(InnerEventMessage(event_type=InnerEventType.POLL_TASK))


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize("memory_db", [False, True], ids=["file_db", "memory_db"])
async def test_persistent_completion_recovers_stopped_approval_mail(tmp_path, monkeypatch, pending, memory_db):
    async with stopped_worker(tmp_path, monkeypatch, pending=pending, memory_db=memory_db) as (
        leader, backend, manager, checkpointer, original_handle, tool
    ):
        assert manager.stopped_approval_checkpoints() == [original_handle.approval_checkpoint]
        # The new default has no copy of the member's real checkpoint.
        CheckpointerFactory.set_default_checkpointer(InMemoryCheckpointer())
        assert await backend.approve_tool("worker", "call-a", True)
        assert await backend.is_team_completed() is None
        assert await backend.settled_completion_snapshot() is not None
        assert await backend.resolve_leader_member_name() == leader.member_name
        assert (await backend.get_member("worker")).status == MemberStatus.STOPPED.value
        assert not await backend.message_manager.get_broadcast_messages(member_name="worker", unread_only=True)
        assert not leader.has_in_flight_round() and not leader.is_agent_running()
        assert not leader.has_pending_interrupt()
        await poll_completion(leader)
        recovery, = manager.recovery_tasks
        await poll_completion(leader)
        await asyncio.wait_for(asyncio.shield(recovery), timeout=5)

        async def mailbox_drained():
            return not await backend.message_manager.get_messages(to_member_name="worker", unread_only=True)

        await eventually(mailbox_drained)
        recovered = manager.spawned_handles["worker"]
        assert recovered is not original_handle
        assert recovered.agent_ref.resources.harness.current_session().checkpointer() is checkpointer

        async def completed():
            await poll_completion(leader)
            return leader._coordination.dispatcher.team_completion._team_completed_emitted

        try:
            await eventually(completed)
        except TimeoutError:
            members = await backend.db.member.get_team_members(backend.team_name)
            unread = await backend.message_manager.members_with_unread([member.member_name for member in members])
            pytest.fail(
                f"Completion stalled with statuses {[(member.member_name, member.status) for member in members]}, "
                f"unread={unread}, goal_open={leader.goal_holds_completion()}, "
                f"leader_pending={leader.has_pending_interrupt()}, "
                f"worker_pending={recovered.agent_ref.has_pending_interrupt()}"
            )
        assert await backend.is_team_completed() is not None
        assert tool.calls == int(pending)
        queue = leader.stream_controller.stream_queue
        output = []
        while (chunk := queue.get_nowait()) is not None:
            output.append(chunk)
        assert any(isinstance(chunk.payload, dict) and chunk.payload.get("event_type") == "team.completed"
                   for chunk in output)
        await leader.pause_coordination()
        assert leader._coordination._lifecycle_state == "paused"
        assert not manager.spawned_handles


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", ["snapshot", "ordinary"])
async def test_stopped_approval_recovery_requires_provenance_and_approval_only(
    tmp_path, monkeypatch, blocker
):
    async with stopped_worker(tmp_path, monkeypatch) as (leader, backend, manager, store, handle, tool):
        assert await backend.approve_tool("worker", "call-a", True)
        if blocker == "snapshot":
            binding = handle.approval_checkpoint
            await store._agent_stores[binding.session_id].clear(binding.agent_id)
        else:
            assert await backend.message_manager.send_message(content="new work", to_member_name="worker")
        await poll_completion(leader)
        assert not manager.recovery_tasks
        assert not manager.spawned_handles
        assert await backend.is_team_completed() is None
        unread = await backend.message_manager.get_messages(to_member_name="worker", unread_only=True)
        assert len(unread) == (2 if blocker == "ordinary" else 1)
        assert tool.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("custom_provider", ["harness", "checkpointer"])
async def test_stopped_approval_recovery_requires_builtin_providers(tmp_path, monkeypatch, custom_provider):
    class CustomHarness(TeamHarness):
        """An extension lacks the built-in harness's checkpoint guarantee."""

    class CustomCheckpointer(InMemoryCheckpointer):
        """An extension may use a different checkpoint ownership contract."""

    checkpointer_type = InMemoryCheckpointer
    if custom_provider == "harness":
        monkeypatch.setattr(agent_configurator, "TeamHarness", CustomHarness)
    else:
        checkpointer_type = CustomCheckpointer
    async with stopped_worker(tmp_path, monkeypatch, checkpointer_type=checkpointer_type) as (
        leader, backend, manager, _, handle, tool
    ):
        assert handle.approval_checkpoint is None
        assert await backend.approve_tool("worker", "call-a", True)
        await poll_completion(leader)
        assert not manager.recovery_tasks
        assert len(await backend.message_manager.get_messages(to_member_name="worker", unread_only=True)) == 1
        assert tool.calls == 0


@pytest.mark.asyncio
async def test_failed_child_restore_keeps_approval_and_releases_restart(tmp_path, monkeypatch):
    async with stopped_worker(tmp_path, monkeypatch) as (leader, backend, manager, store, original, tool):
        pre_execute = store.pre_agent_execute
        calls = []

        async def failed_restore(session, inputs):
            if session.agent_id() == original.approval_checkpoint.agent_id:
                calls.append(session.agent_id())
                raise ValueError("unreadable child snapshot")
            await pre_execute(session, inputs)

        monkeypatch.setattr(store, "pre_agent_execute", failed_restore)
        assert await backend.approve_tool("worker", "call-a", True)
        await poll_completion(leader)
        await asyncio.gather(*manager.recovery_tasks)
        await poll_completion(leader)

        assert len(calls) == 1
        assert not manager.recovery_tasks and not manager.spawned_handles
        assert (await backend.get_member("worker")).status == MemberStatus.STOPPED.value
        assert len(await backend.message_manager.get_messages(to_member_name="worker", unread_only=True)) == 1
        assert tool.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("teardown", ["pause_coordination", "stop_coordination"])
async def test_teardown_releases_a_claim_committed_during_cancellation(tmp_path, monkeypatch, teardown):
    async with stopped_worker(tmp_path, monkeypatch) as (leader, backend, manager, _, _, tool):
        dao = backend.db.member
        claim = backend.db.claim_approval_restart
        committed = asyncio.Event()
        finish_claim = asyncio.Event()

        async def delayed_result(*args):
            result = await claim(*args)
            assert result
            committed.set()
            await finish_claim.wait()
            return result

        monkeypatch.setattr(backend.db, "claim_approval_restart", delayed_result)
        assert await backend.approve_tool("worker", "call-a", True)
        await poll_completion(leader)
        task, = manager.recovery_tasks
        await asyncio.wait_for(committed.wait(), 2)

        teardown_task = asyncio.create_task(getattr(leader, teardown)())
        try:
            async with asyncio.timeout(2):
                while not task.cancelling():
                    await asyncio.sleep(0.01)
            assert not manager._approval_recovery_open
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0)
        finally:
            finish_claim.set()
            await asyncio.wait_for(teardown_task, 2)

        assert not manager.recovery_tasks and not manager.spawned_handles
        assert (await backend.get_member("worker")).status == MemberStatus.STOPPED.value
        assert len(await backend.message_manager.get_messages(to_member_name="worker", unread_only=True)) == 1
        assert tool.calls == 0


@pytest.mark.asyncio
async def test_unauthorized_approval_does_not_start_a_stopped_member(tmp_path, monkeypatch):
    async with stopped_worker(tmp_path, monkeypatch) as (leader, backend, manager, _, _, tool):
        assert await backend.message_manager.send_message(
            content='{"type": "tool_approval_result", "tool_call_id": "call-a", "approved": true}',
            to_member_name="worker",
            from_member_name="worker",
            protocol="json",
        )
        await poll_completion(leader)
        assert not manager.recovery_tasks and not manager.spawned_handles
        assert len(await backend.message_manager.get_messages(to_member_name="worker", unread_only=True)) == 1
        assert tool.calls == 0


@pytest.mark.asyncio
async def test_failed_transport_start_keeps_approval_and_never_marks_ready(tmp_path, monkeypatch):
    async with stopped_worker(tmp_path, monkeypatch) as (leader, backend, manager, _, _, tool):
        kernel_type = type(leader._coordination)
        subscribe = kernel_type.subscribe_transport
        statuses = []

        async def fail_worker_subscription(self, team_name):
            if self._host.member_name == "worker":
                statuses.append(await self._host.team_member.status())
                raise RuntimeError("cannot subscribe the restored worker")
            await subscribe(self, team_name)

        monkeypatch.setattr(kernel_type, "subscribe_transport", fail_worker_subscription)
        assert await backend.approve_tool("worker", "call-a", True)
        await poll_completion(leader)
        await asyncio.gather(*manager.recovery_tasks)

        assert statuses == [MemberStatus.RESTARTING]
        assert not manager.spawned_handles
        assert (await backend.get_member("worker")).status == MemberStatus.STOPPED.value
        assert len(await backend.message_manager.get_messages(to_member_name="worker", unread_only=True)) == 1
        assert tool.calls == 0


@pytest.mark.asyncio
async def test_stop_rolls_back_ready_written_during_cancelled_startup(tmp_path, monkeypatch):
    async with stopped_worker(tmp_path, monkeypatch) as (leader, backend, manager, _, _, tool):
        dao_type = type(backend.db.member)
        transition = dao_type.try_transition_member_status
        ready_written = asyncio.Event()
        finish_ready = asyncio.Event()

        async def delay_ready_result(self, member, team, expected, target):
            result = await transition(self, member, team, expected, target)
            if member == "worker" and (expected, target) == (MemberStatus.RESTARTING, MemberStatus.READY):
                assert result
                ready_written.set()
                await finish_ready.wait()
            return result

        monkeypatch.setattr(dao_type, "try_transition_member_status", delay_ready_result)
        assert await backend.approve_tool("worker", "call-a", True)
        await poll_completion(leader)

        await asyncio.wait_for(ready_written.wait(), 2)
        handle = manager.spawned_handles["worker"]
        assert (await backend.get_member("worker")).status == MemberStatus.READY.value
        assert not handle.agent_ref._coordination.is_running
        stop = asyncio.create_task(leader.stop_coordination())
        try:
            async with asyncio.timeout(2):
                while not handle._task.cancelling():
                    await asyncio.sleep(0.01)
        finally:
            finish_ready.set()
            await asyncio.wait_for(stop, 2)

        assert not manager.spawned_handles and not handle.is_alive
        assert (await backend.get_member("worker")).status == MemberStatus.STOPPED.value
        assert len(await backend.message_manager.get_messages(to_member_name="worker", unread_only=True)) == 1
        assert tool.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("memory_db", [False, True], ids=["file_db", "memory_db"])
@pytest.mark.parametrize("arrival", ["broadcast", "direct", "approval", "group", "notification"])
async def test_approval_claim_rechecks_mail_and_releases_only_ineligible_attempt(
    tmp_path, monkeypatch, memory_db, arrival,
):
    async with stopped_worker(tmp_path, monkeypatch, memory_db=memory_db) as (
        leader, backend, manager, _, original, tool
    ):
        claim = backend.db.claim_approval_restart
        entered, release = asyncio.Event(), asyncio.Event()

        async def gated_claim(*args):
            entered.set()
            await release.wait()
            return await claim(*args)

        monkeypatch.setattr(backend.db, "claim_approval_restart", gated_claim)
        assert await backend.approve_tool("worker", "call-a", True)
        await poll_completion(leader)
        recovery, = manager.recovery_tasks
        try:
            await asyncio.wait_for(entered.wait(), 2)
            if arrival == "broadcast":
                extra = await backend.message_manager.broadcast_message("new ordinary work")
            elif arrival == "direct":
                extra = await backend.message_manager.send_message("new ordinary work", "worker")
            elif arrival in {"group", "notification"}:
                extra = "late-group"
                assert await backend.db.message.create_message(
                    extra, backend.team_name, "leader", "group work", broadcast=True,
                    meta={"type": "group_chat", "mentions": ["worker"] if arrival == "group" else []},
                )
                if arrival == "notification":
                    assert await backend.db.message.create_group_notification(
                        "late-notice", backend.team_name, "worker", extra,
                    )
            else:
                assert await backend.approve_tool("worker", "call-b", True)
                extra = None
        finally:
            release.set()
        assert await asyncio.wait_for(asyncio.shield(recovery), 3) is False
        await asyncio.sleep(0)
        assert (await backend.get_member("worker")).status == MemberStatus.STOPPED.value
        assert not manager.spawned_handles and "worker" not in manager._approval_recovery_attempts
        assert tool.calls == 0
        unread = await backend.message_manager.get_messages(to_member_name="worker", unread_only=True)
        assert unread
        if extra:
            await backend.message_manager.mark_message_read(extra, "worker")
        await poll_completion(leader)
        retry, = manager.recovery_tasks
        assert await asyncio.wait_for(asyncio.shield(retry), 5) is True

        async def drained():
            return not await backend.message_manager.get_messages(to_member_name="worker", unread_only=True)

        await eventually(drained)
        assert manager.spawned_handles["worker"] is not original
        assert tool.calls == 0


@pytest.mark.asyncio
async def test_postclaim_broadcast_before_ready_uses_normal_mailbox(tmp_path, monkeypatch):
    async with stopped_worker(tmp_path, monkeypatch) as (leader, backend, manager, _, _, tool):
        entered, release, delivered = asyncio.Event(), asyncio.Event(), asyncio.Event()
        transition = backend.db.member.try_transition_member_status
        deliver = TeamAgent.deliver_input
        marker = "POSTCLAIM_BROADCAST"

        async def gated_ready(member, team, old, new):
            if member == "worker" and old == MemberStatus.RESTARTING and new == MemberStatus.READY:
                entered.set()
                await release.wait()
            return await transition(member, team, old, new)

        async def observe_input(self, *args, **kwargs):
            if self.member_name == "worker" and marker in str((args, kwargs)):
                delivered.set()
            return await deliver(self, *args, **kwargs)

        monkeypatch.setattr(backend.db.member, "try_transition_member_status", gated_ready)
        monkeypatch.setattr(TeamAgent, "deliver_input", observe_input)
        assert await backend.approve_tool("worker", "call-a", True)
        await poll_completion(leader)
        recovery, = manager.recovery_tasks
        try:
            await asyncio.wait_for(entered.wait(), 3)
            assert (await backend.get_member("worker")).status == MemberStatus.RESTARTING.value
            assert await backend.message_manager.broadcast_message(marker)
        finally:
            release.set()
        await asyncio.wait_for(asyncio.shield(recovery), 5)
        await asyncio.wait_for(delivered.wait(), 3)


@pytest.mark.asyncio
async def test_unexpected_claim_failure_keeps_this_runs_attempt(tmp_path, monkeypatch):
    async with stopped_worker(tmp_path, monkeypatch) as (leader, backend, manager, _, _, tool):
        async def failed_claim(*args):
            raise RuntimeError("unknown transaction result")

        monkeypatch.setattr(backend.db, "claim_approval_restart", failed_claim)
        assert await backend.approve_tool("worker", "call-a", True)
        await poll_completion(leader)
        recovery, = manager.recovery_tasks
        assert await recovery is True
        await asyncio.sleep(0)
        assert "worker" in manager._approval_recovery_attempts
        await poll_completion(leader)
        assert not manager.recovery_tasks and not manager.spawned_handles
        assert (await backend.get_member("worker")).status == MemberStatus.STOPPED.value
        assert tool.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("covered", [False, True], ids=["unmentioned", "covered_by_watermark"])
async def test_approval_claim_reuses_group_addressing_and_watermark(tmp_path, monkeypatch, covered):
    async with stopped_worker(tmp_path, monkeypatch) as (leader, backend, manager, _, _, tool):
        assert await backend.approve_tool("worker", "call-a", True)
        unread = await backend.message_manager.get_messages(to_member_name="worker", unread_only=True)
        assert await backend.db.message.create_message(
            "old-group", backend.team_name, "leader", "group input", broadcast=True,
            meta={"type": "group_chat", "mentions": ["worker"] if covered else []},
        )
        if covered:
            assert await backend.message_manager.mark_message_read("old-group", "worker")
        assert await backend.db.claim_approval_restart(
            backend.team_name, "worker", "leader", frozenset(row.message_id for row in unread),
        )
        assert (await backend.get_member("worker")).status == MemberStatus.RESTARTING.value
        assert not manager.spawned_handles and tool.calls == 0
        assert await backend.db.member.try_transition_member_status(
            "worker", backend.team_name, MemberStatus.RESTARTING, MemberStatus.STOPPED,
        )
